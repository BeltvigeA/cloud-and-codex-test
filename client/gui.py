"""Simple GUI application for listening to channels and logging data to JSON."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import socket
import ssl
import string
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from ipaddress import ip_address
from queue import Empty, Queue
from typing import Any, Callable, Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests


hardcodedBaseUrl = "https://printer-backend-934564650450.europe-west1.run.app"
hardcodedApiKey = (
    "V9JDvmqG9SB40JpmNu1HwM8ZbvplTrf7ddjudAe6yvjg7hbENEgA429N6xuio4CWQ7nv30fk0c2V8WiOemNWuP2PCKa9dbp7Aoww5lfQPdQu1FGuNKgUZ4wmA23sFCQ7lpxRq9cgZdIWMmwY2EpeYCR13UMgUzDqE8Su6GDPXuXHuPcMKxZnrI9vKNFjtxtCymw1Q8Wr"
)
hardcodedOutputDirectory = str(Path.home() / ".printmaster" / "files")
hardcodedJsonLogFile = str(Path.home() / ".printmaster" / "listener-log.json")
hardcodedPollIntervalSeconds = 30


def _generateRecipientId(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def loadOrCreateRecipientId() -> str:
    clientInfoPath = Path.home() / ".printmaster" / "client-info.json"
    try:
        clientInfoPath.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logging.warning("Unable to prepare client info directory: %s", error)

    clientInfoData: Dict[str, Any] = {}
    recipientId: Optional[str] = None

    if clientInfoPath.exists():
        try:
            with clientInfoPath.open("r", encoding="utf-8") as handle:
                loadedData = json.load(handle)
            if isinstance(loadedData, dict):
                clientInfoData = loadedData
                storedRecipientId = clientInfoData.get("recipientId")
                if isinstance(storedRecipientId, str) and storedRecipientId.strip():
                    recipientId = storedRecipientId.strip()
        except (OSError, json.JSONDecodeError) as error:
            logging.warning("Failed to read client info file: %s", error)

    if not recipientId:
        recipientId = _generateRecipientId()
        clientInfoData["recipientId"] = recipientId
        try:
            with clientInfoPath.open("w", encoding="utf-8") as handle:
                json.dump(clientInfoData, handle, indent=2, sort_keys=True)
        except OSError as error:
            logging.error("Failed to write client info file: %s", error)

    return recipientId


def addPrinterIdentityToPayload(
    payload: Dict[str, Any], printerSerial: Optional[str], accessCode: Optional[str]
) -> Dict[str, Any]:
    if printerSerial:
        payload["printerSerial"] = printerSerial
    if accessCode:
        payload["accessCode"] = accessCode
    return payload

from .autoprint.brake_flow import BrakeFlowContext
from .bambuPrinter import BambuPrintOptions, postStatus, sendBambuPrintJob
from .status_subscriber import BambuStatusSubscriber
from .command_controller import CommandWorker
from .client import (
    appendJsonLogEntry,
    buildBaseUrl,
    configureLogging,
    defaultBaseUrl,
    defaultFilesDirectory,
    ensureOutputDirectory,
    getPrinterStatusEndpointUrl,
    interpretBoolean,
    interpretInteger,
    listenForFiles,
    extractPreferredTransport,
    registerPrintersConfigChangedListener,
)


def loadPrinters() -> list[Dict[str, Any]]:
    path = os.path.expanduser("~/.printmaster/printers.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def pickPrinter(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    printers = loadPrinters()
    if not printers:
        return None
    serialNumber = (metadata or {}).get("serialNumber")
    nickname = (metadata or {}).get("nickname")
    if isinstance(serialNumber, str) and serialNumber.strip():
        normalizedSerial = serialNumber.strip()
        for printer in printers:
            if str(printer.get("serialNumber", "")).strip() == normalizedSerial:
                return printer
    if isinstance(nickname, str) and nickname.strip():
        normalizedNickname = nickname.strip().lower()
        for printer in printers:
            printerNickname = str(printer.get("nickname") or "").strip().lower()
            if printerNickname == normalizedNickname:
                return printer
    return printers[0]



class ListenerGuiApp:
    def __init__(self) -> None:
        configureLogging()
        self.root = tk.Tk()
        self.root.title("Cloud Printer Listener")
        self.root.geometry("560x420")
        self.root.protocol("WM_DELETE_WINDOW", self._handleWindowClose)

        self.bambuModelOptions = [
            "X1 Carbon",
            "X1E",
            "X1",
            "P1S",
            "P1P",
            "A1",
            "A1 Mini",
        ]
        self.bambuModelCanonicalMap = {model.lower(): model for model in self.bambuModelOptions}
        self.bambuConnectMethod = "bambu_connect"
        self.defaultConnectionMethod = "octoprint"
        self.mqttConnectionMethod = "mqtt"
        self.connectionMethodOptions = [
            self.defaultConnectionMethod,
            self.mqttConnectionMethod,
            self.bambuConnectMethod,
        ]

        self.logQueue: "Queue[str]" = Queue()
        self.listenerThread: Optional[threading.Thread] = None
        self.stopEvent: Optional[threading.Event] = None
        self.logFilePath: Optional[Path] = None

        self.printerStoragePath = Path.home() / ".printmaster" / "printers.json"
        self.printers: list[Dict[str, Any]] = self._loadPrinters()
        self.printerStatusQueue: "Queue[tuple[str, Any]]" = Queue()
        self.statusRefreshThread: Optional[threading.Thread] = None
        self.statusRefreshIntervalMs = 60_000
        self.pendingImmediateStatusRefresh = False

        self.listenerRecipientId = loadOrCreateRecipientId()
        self.listenerStatusApiKey = hardcodedApiKey
        self.listenerControlApiKey = hardcodedApiKey
        self._managedEnvKeys: set[str] = set()

        self.activePrinterDialog: Optional[Dict[str, Any]] = None

        self.liveStatusEnabledVar = tk.BooleanVar(value=True)
        self.statusSubscriber = BambuStatusSubscriber(
            onUpdate=self._onPrinterStatusUpdate,
            onError=self._onPrinterStatusError,
            logger=logging.getLogger(__name__),
        )
        self.lastLiveStatusAlerts: Dict[str, str] = {}
        self.commandWorkers: Dict[str, CommandWorker] = {}

        self._buildLayout()
        self.root.after(200, self._processLogQueue)
        self.root.after(200, self._processPrinterStatusUpdates)
        self._scheduleStatusRefresh(0)
        self._registerPrintersConfigListener()

    def log(self, message: str) -> None:
        self.logQueue.put(str(message))

    def _buildLayout(self) -> None:
        paddingOptions = {"padx": 8, "pady": 4}

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True)

        listenerFrame = ttk.Frame(notebook)
        notebook.add(listenerFrame, text="Listener")
        self._buildListenerTab(listenerFrame, paddingOptions)

        printersFrame = ttk.Frame(notebook)
        notebook.add(printersFrame, text="3D Printers")
        self._buildPrintersTab(printersFrame)

    def _buildListenerTab(self, parent: ttk.Frame, paddingOptions: Dict[str, int]) -> None:
        self.baseUrlVar = tk.StringVar(value=hardcodedBaseUrl)
        self.recipientVar = tk.StringVar(value=self.listenerRecipientId)
        self.statusApiKeyVar = tk.StringVar(value=hardcodedApiKey)
        self.controlApiKeyVar = tk.StringVar(value=hardcodedApiKey)
        self.outputDirVar = tk.StringVar(value=hardcodedOutputDirectory)
        self.logFileVar = tk.StringVar(value=hardcodedJsonLogFile)
        self.pollIntervalVar = tk.IntVar(value=hardcodedPollIntervalSeconds)
        self.liveStatusEnabledVar.set(True)

        self.recipientVar.trace_add("write", lambda *_: self._updateListenerRecipient())
        self.statusApiKeyVar.trace_add("write", lambda *_: self._updateListenerStatusApiKey())
        self.controlApiKeyVar.trace_add("write", lambda *_: self._updateListenerControlApiKey())

        currentRow = 0
        ttk.Label(parent, text="Channel (Recipient ID):").grid(
            row=currentRow, column=0, sticky=tk.W, **paddingOptions
        )
        recipientFrame = ttk.Frame(parent)
        recipientFrame.grid(row=currentRow, column=1, sticky=tk.EW, **paddingOptions)
        self.recipientEntry = ttk.Entry(
            recipientFrame,
            textvariable=self.recipientVar,
            width=40,
            show="•",
            state="readonly",
        )
        self.recipientEntry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.recipientVisibleVar = tk.BooleanVar(value=False)

        def toggleRecipientVisibility() -> None:
            self.recipientVisibleVar.set(not self.recipientVisibleVar.get())
            self.refreshRecipientVisibility()

        self.recipientToggleButton = ttk.Button(
            recipientFrame, text="Show", command=toggleRecipientVisibility
        )
        self.recipientToggleButton.pack(side=tk.LEFT, padx=4)
        ttk.Button(recipientFrame, text="Copy", command=self.copyRecipientIdToClipboard).pack(
            side=tk.LEFT
        )

        currentRow += 1
        ttk.Label(
            parent,
            text="Connection settings are preconfigured for this installation.",
        ).grid(row=currentRow, column=0, columnspan=2, sticky=tk.W, **paddingOptions)

        currentRow += 1
        buttonFrame = ttk.Frame(parent)
        buttonFrame.grid(row=currentRow, column=0, columnspan=2, pady=12)
        self.startButton = ttk.Button(buttonFrame, text="Start Listening", command=self.startListening)
        self.startButton.pack(side=tk.LEFT, padx=6)
        self.stopButton = ttk.Button(buttonFrame, text="Stop", command=self.stopListening, state=tk.DISABLED)
        self.stopButton.pack(side=tk.LEFT, padx=6)

        currentRow += 1
        ttk.Label(parent, text="Event Log:").grid(row=currentRow, column=0, sticky=tk.W, **paddingOptions)
        self.logText = tk.Text(parent, height=10, state=tk.DISABLED)
        self.logText.grid(row=currentRow, column=1, sticky=tk.NSEW, **paddingOptions)

        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(currentRow, weight=1)

        self._updateListenerRecipient()
        self._updateListenerStatusApiKey()
        self._updateListenerControlApiKey()
        self.refreshRecipientVisibility()

    def refreshRecipientVisibility(self) -> None:
        entryWidget = getattr(self, "recipientEntry", None)
        toggleButton = getattr(self, "recipientToggleButton", None)
        visibleVar = getattr(self, "recipientVisibleVar", None)
        if entryWidget is None or not isinstance(visibleVar, tk.BooleanVar):
            return

        if visibleVar.get():
            entryWidget.configure(show="")
            if toggleButton is not None:
                toggleButton.configure(text="Hide")
        else:
            entryWidget.configure(show="•")
            if toggleButton is not None:
                toggleButton.configure(text="Show")

    def copyRecipientIdToClipboard(self) -> None:
        recipientValue = self.recipientVar.get().strip() if hasattr(self, "recipientVar") else ""
        if not recipientValue:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(recipientValue)
            self.root.update_idletasks()
            self.logQueue.put("Recipient ID copied to clipboard.")
        except Exception:
            logging.exception("Failed to copy recipient ID to clipboard")

    def _buildPrintersTab(self, parent: ttk.Frame) -> None:
        self.printerSearchVar = tk.StringVar()
        self.printerBrandOptions = [
            "Bambu Lab",
            "Creality",
            "Prusa Research",
            "Anycubic",
            "Flashforge",
            "Ultimaker",
            "MakerBot",
            "Formlabs",
        ]
        self.printerStatusOptions = [
            "Unknown",
            "Online",
            "Idle",
            "Printing",
            "Paused",
            "Completed",
            "Error",
            "Offline",
        ]

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        searchFrame = ttk.Frame(parent)
        searchFrame.grid(row=0, column=0, sticky=tk.EW, padx=8, pady=(8, 4))
        ttk.Label(searchFrame, text="Search by Name or IP:").pack(side=tk.LEFT)
        searchEntry = ttk.Entry(searchFrame, textvariable=self.printerSearchVar, width=30)
        searchEntry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(searchFrame, text="Clear", command=self._clearPrinterSearch).pack(side=tk.LEFT)
        self.printerSearchVar.trace_add("write", lambda *_: self._refreshPrinterList())

        actionFrame = ttk.Frame(parent)
        actionFrame.grid(row=1, column=0, sticky=tk.EW, padx=8, pady=4)
        ttk.Button(actionFrame, text="Add Printer", command=self._openAddPrinterDialog).pack(
            side=tk.LEFT
        )
        self.editPrinterButton = ttk.Button(
            actionFrame,
            text="Edit Selected",
            command=self._openEditPrinterDialog,
            state=tk.DISABLED,
        )
        self.editPrinterButton.pack(side=tk.LEFT, padx=(8, 0))
        self.sendTestStatusButton = ttk.Button(
            actionFrame,
            text="Send Test Status",
            command=self.openManualStatusDialog,
            state=tk.DISABLED,
        )
        self.sendTestStatusButton.pack(side=tk.LEFT, padx=8)
        self.connectPrintersButton = ttk.Button(
            actionFrame,
            text="Connect Printers",
            command=self.refreshPrintersNow,
            state=tk.NORMAL,
        )
        self.connectPrintersButton.pack(side=tk.LEFT, padx=8)
        self.captureReferenceButton = ttk.Button(
            actionFrame,
            text="Capture Bed Reference",
            command=self._captureSelectedBedReference,
            state=tk.DISABLED,
        )
        self.captureReferenceButton.pack(side=tk.LEFT, padx=8)
        self.runBrakeDemoButton = ttk.Button(
            actionFrame,
            text="Run Brake Demo",
            command=self._runBrakeDemoForSelected,
            state=tk.DISABLED,
        )
        self.runBrakeDemoButton.pack(side=tk.LEFT, padx=8)
        actionFrame.columnconfigure(0, weight=1)

        treeFrame = ttk.Frame(parent)
        treeFrame.grid(row=2, column=0, sticky=tk.NSEW, padx=8, pady=(4, 8))
        columns = (
            "nickname",
            "ipAddress",
            "accessCode",
            "serialNumber",
            "brand",
            "bambuModel",
            "connectionMethod",
            "status",
            "nozzleTemp",
            "bedTemp",
            "progress",
        )
        self.printerTree = ttk.Treeview(treeFrame, columns=columns, show="headings", selectmode="browse")
        self.printerTree.heading("nickname", text="Nickname")
        self.printerTree.heading("ipAddress", text="IP Address")
        self.printerTree.heading("accessCode", text="Access Code")
        self.printerTree.heading("serialNumber", text="Serial Number")
        self.printerTree.heading("brand", text="Brand")
        self.printerTree.heading("bambuModel", text="Model")
        self.printerTree.heading("connectionMethod", text="Connection")
        self.printerTree.heading("status", text="Status")
        self.printerTree.heading("nozzleTemp", text="Nozzle Temp")
        self.printerTree.heading("bedTemp", text="Bed Temp")
        self.printerTree.heading("progress", text="Progress")
        self.printerTree.column("nickname", width=120)
        self.printerTree.column("ipAddress", width=110)
        self.printerTree.column("accessCode", width=110)
        self.printerTree.column("serialNumber", width=120)
        self.printerTree.column("brand", width=100)
        self.printerTree.column("bambuModel", width=110)
        self.printerTree.column("connectionMethod", width=120)
        self.printerTree.column("status", width=100)
        self.printerTree.column("nozzleTemp", width=110)
        self.printerTree.column("bedTemp", width=100)
        self.printerTree.column("progress", width=140)

        scrollbar = ttk.Scrollbar(treeFrame, orient=tk.VERTICAL, command=self.printerTree.yview)
        self.printerTree.configure(yscrollcommand=scrollbar.set)
        self.printerTree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.printerTree.bind("<<TreeviewSelect>>", self._onPrinterSelection)

        self._refreshPrinterList()

    def _registerPrintersConfigListener(self) -> None:
        try:
            registerPrintersConfigChangedListener(self._handlePrintersConfigChange)
        except Exception:
            logging.exception("Failed to register printers config listener")

    def _loadPrinters(self) -> list[Dict[str, Any]]:
        try:
            self.printerStoragePath.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            logging.warning("Unable to prepare printer storage directory: %s", error)
        if self.printerStoragePath.exists():
            try:
                with self.printerStoragePath.open("r", encoding="utf-8") as printerFile:
                    loadedPrinters = json.load(printerFile)
                if isinstance(loadedPrinters, list):
                    sanitizedPrinters: list[Dict[str, Any]] = []
                    for entry in loadedPrinters:
                        if not isinstance(entry, dict):
                            continue
                        sanitizedPrinters.append(
                            self._applyTelemetryDefaults(
                                {
                                    "nickname": str(entry.get("nickname", "")),
                                    "ipAddress": str(entry.get("ipAddress", "")),
                                    "accessCode": str(entry.get("accessCode", "")),
                                    "serialNumber": str(entry.get("serialNumber", "")),
                                    "brand": str(entry.get("brand", "")),
                                    "bambuModel": self._parseOptionalString(entry.get("bambuModel")) or "",
                                    "connectionMethod": self._parseOptionalString(entry.get("connectionMethod")),
                                    "transport": self._parseOptionalString(entry.get("transport")),
                                    "useCloud": interpretBoolean(entry.get("useCloud")),
                                    "status": str(entry.get("status", "")) or "Unknown",
                                    "nozzleTemp": self._parseOptionalFloat(entry.get("nozzleTemp")),
                                    "bedTemp": self._parseOptionalFloat(entry.get("bedTemp")),
                                    "progressPercent": self._parseOptionalFloat(entry.get("progressPercent")),
                                    "remainingTimeSeconds": self._parseOptionalInt(entry.get("remainingTimeSeconds")),
                                    "gcodeState": self._parseOptionalString(entry.get("gcodeState")),
                                    "manualStatusDefaults": entry.get("manualStatusDefaults"),
                                }
                            )
                        )
                    return sanitizedPrinters
            except (OSError, json.JSONDecodeError) as error:
                logging.warning("Unable to load printers from %s: %s", self.printerStoragePath, error)
        return []

    def _extractNumericCandidate(self, value: Any) -> Any:
        if isinstance(value, dict):
            preferredKeys = ("current", "value", "actual", "temperature", "temper", "target")
            for key in preferredKeys:
                if key in value:
                    nested = self._extractNumericCandidate(value.get(key))
                    if nested is not None:
                        return nested
            for nestedValue in value.values():
                nested = self._extractNumericCandidate(nestedValue)
                if nested is not None:
                    return nested
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                nested = self._extractNumericCandidate(item)
                if nested is not None:
                    return nested
            return None
        return value

    def _parseOptionalFloat(self, value: Any) -> Optional[float]:
        candidateValue = self._extractNumericCandidate(value)
        if isinstance(candidateValue, (int, float)) and not isinstance(candidateValue, bool):
            return float(candidateValue)
        if isinstance(candidateValue, str):
            candidate = candidateValue.strip().replace("°C", "")
            candidate = candidate.replace("°c", "").replace("°", "")
            if candidate:
                try:
                    return float(candidate)
                except ValueError:
                    return None
        return None

    def _parseOptionalInt(self, value: Any) -> Optional[int]:
        candidateValue = self._extractNumericCandidate(value)
        if isinstance(candidateValue, bool):
            return None
        if isinstance(candidateValue, int):
            return candidateValue
        if isinstance(candidateValue, float):
            return int(candidateValue)
        if isinstance(candidateValue, str):
            candidate = (
                candidateValue.strip()
                .lower()
                .replace("seconds", "")
                .replace("second", "")
                .replace("minutes", "")
                .replace("minute", "")
                .replace("hrs", "")
                .replace("hr", "")
                .replace("hours", "")
                .replace("hour", "")
                .replace("s", "")
                .replace("m", "")
                .replace("h", "")
            )
            candidate = candidate.strip()
            if candidate:
                try:
                    return int(float(candidate))
                except ValueError:
                    return None
        return None

    def _parseOptionalString(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value)

    def _applyTelemetryDefaults(self, printerDetails: Dict[str, Any]) -> Dict[str, Any]:
        brandValue = self._parseOptionalString(printerDetails.get("brand")) or ""
        printerDetails["brand"] = brandValue

        bambuOptions = list(getattr(self, "bambuModelOptions", []))
        bambuOptionsMap = getattr(self, "bambuModelCanonicalMap", {})
        if not bambuOptionsMap and bambuOptions:
            bambuOptionsMap = {model.lower(): model for model in bambuOptions}
        bambuConnect = getattr(self, "bambuConnectMethod", "bambu_connect")
        defaultTransport = getattr(self, "defaultConnectionMethod", "octoprint")
        mqttTransport = getattr(self, "mqttConnectionMethod", "mqtt")

        modelCandidate = self._parseOptionalString(printerDetails.get("bambuModel")) or ""
        canonicalModel = bambuOptionsMap.get(modelCandidate.lower(), modelCandidate)
        connectionCandidate = self._parseOptionalString(printerDetails.get("connectionMethod")) or ""
        normalizedConnection = connectionCandidate.lower()
        wasLanConnection = normalizedConnection == "lan"
        if normalizedConnection == "legacy":
            normalizedConnection = bambuConnect
        if wasLanConnection:
            normalizedConnection = mqttTransport

        isBambuBrand = bool(brandValue and "bambu" in brandValue.lower())
        if not isBambuBrand:
            printerDetails["bambuModel"] = ""
            printerDetails["connectionMethod"] = (
                mqttTransport if normalizedConnection == mqttTransport else defaultTransport
            )
        else:
            canonicalModel = bambuOptionsMap.get(modelCandidate.lower(), "")
            if not canonicalModel and not modelCandidate and bambuOptions:
                canonicalModel = bambuOptions[0]

            supportedModels = set(bambuOptionsMap.keys()) or {option.lower() for option in bambuOptions}
            normalizedModelKey = canonicalModel.lower() if canonicalModel else ""
            isSupportedModel = bool(normalizedModelKey and normalizedModelKey in supportedModels)

            if not isSupportedModel:
                normalizedConnection = mqttTransport
            elif normalizedConnection not in {mqttTransport, bambuConnect}:
                normalizedConnection = bambuConnect

            printerDetails["bambuModel"] = canonicalModel if isSupportedModel else ""
            resolvedConnectionMethod = (
                bambuConnect
                if normalizedConnection == bambuConnect and isSupportedModel
                else mqttTransport
            )
            if wasLanConnection:
                resolvedConnectionMethod = "lan"
            printerDetails["connectionMethod"] = resolvedConnectionMethod

        printerDetails["status"] = str(printerDetails.get("status", "")) or "Unknown"
        printerDetails["nozzleTemp"] = self._parseOptionalFloat(printerDetails.get("nozzleTemp"))
        printerDetails["bedTemp"] = self._parseOptionalFloat(printerDetails.get("bedTemp"))
        printerDetails["progressPercent"] = self._parseOptionalFloat(printerDetails.get("progressPercent"))
        printerDetails["remainingTimeSeconds"] = self._parseOptionalInt(
            printerDetails.get("remainingTimeSeconds")
        )
        printerDetails["gcodeState"] = self._parseOptionalString(printerDetails.get("gcodeState"))
        for base44Key in ("statusBaseUrl", "statusApiKey", "statusRecipientId"):
            if base44Key in printerDetails:
                printerDetails.pop(base44Key, None)
        printerDetails["manualStatusDefaults"] = self._sanitizeManualStatusDefaults(
            printerDetails.get("manualStatusDefaults")
        )

        if printerDetails.get("connectionMethod") == bambuConnect:
            printerDetails.setdefault("transport", "bambu_connect")
            printerDetails.setdefault("useCloud", True)
        elif printerDetails.get("connectionMethod") == "lan":
            printerDetails.setdefault("transport", "lan")
            printerDetails.setdefault("useCloud", False)

        return printerDetails

    def _sanitizeManualStatusDefaults(self, value: Any) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        if not isinstance(value, dict):
            return sanitized

        for field in ("publicKey", "objectName", "productName", "printJobId", "status"):
            fieldValue = value.get(field)
            if fieldValue is not None:
                sanitized[field] = str(fieldValue).strip()

        if "useAms" in value:
            useAmsValue = value.get("useAms")
            interpretedUseAms: Optional[bool]
            if isinstance(useAmsValue, bool):
                interpretedUseAms = useAmsValue
            elif isinstance(useAmsValue, (int, float)):
                interpretedUseAms = bool(useAmsValue)
            elif isinstance(useAmsValue, str):
                normalized = useAmsValue.strip().lower()
                if normalized in {"true", "1", "yes", "y", "on"}:
                    interpretedUseAms = True
                elif normalized in {"false", "0", "no", "n", "off"}:
                    interpretedUseAms = False
                elif normalized in {"auto", "", "none", "null"}:
                    interpretedUseAms = None
                else:
                    interpretedUseAms = None
            else:
                interpretedUseAms = None
            sanitized["useAms"] = interpretedUseAms

        platesRequested = self._parseOptionalInt(value.get("platesRequested"))
        if platesRequested is not None and platesRequested > 0:
            sanitized["platesRequested"] = platesRequested

        jobProgress = self._parseOptionalFloat(value.get("jobProgress"))
        if jobProgress is not None and jobProgress >= 0:
            sanitized["jobProgress"] = jobProgress

        nozzleTemp = self._parseOptionalFloat(value.get("nozzleTemp"))
        if nozzleTemp is not None:
            sanitized["nozzleTemp"] = nozzleTemp

        bedTemp = self._parseOptionalFloat(value.get("bedTemp"))
        if bedTemp is not None:
            sanitized["bedTemp"] = bedTemp

        materialLevel = value.get("materialLevel")
        if isinstance(materialLevel, dict):
            sanitized["materialLevel"] = materialLevel

        return sanitized

    def _formatTemperature(self, value: Optional[float]) -> str:
        if value is None:
            return "-"
        try:
            numericValue = float(value)
        except (TypeError, ValueError):
            return "-"
        return f"{numericValue:.1f}°C"

    def _formatOptionalNumber(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return ("%g" % float(value)).strip()
        if isinstance(value, str):
            return value.strip()
        return str(value)

    def _formatMaterialLevelForEntry(self, value: Any) -> str:
        if isinstance(value, dict) and value:
            try:
                return json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                return ""
        return ""

    def _formatDuration(self, seconds: int) -> str:
        if seconds < 0:
            return "-"
        minutes, remainingSeconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        segments: list[str] = []
        if hours:
            segments.append(f"{hours}h")
        if minutes:
            segments.append(f"{minutes}m")
        if remainingSeconds and not hours:
            segments.append(f"{remainingSeconds}s")
        return " ".join(segments) if segments else "0s"

    def _formatProgress(
        self,
        percent: Optional[float],
        remainingSeconds: Optional[int],
        state: Optional[str],
    ) -> str:
        if percent is None and remainingSeconds is None and not state:
            return "-"
        parts: list[str] = []
        if percent is not None:
            parts.append(f"{percent:.0f}%")
        if remainingSeconds is not None:
            parts.append(self._formatDuration(max(0, remainingSeconds)))
        if state:
            normalizedState = state.title() if state.isupper() else state
            parts.append(normalizedState)
        return " | ".join(parts)

    def _savePrinters(self) -> None:
        try:
            self.printerStoragePath.parent.mkdir(parents=True, exist_ok=True)
            with self.printerStoragePath.open("w", encoding="utf-8") as printerFile:
                json.dump(self.printers, printerFile, ensure_ascii=False, indent=2)
        except OSError as error:
            logging.exception("Failed to save printers: %s", error)
            messagebox.showerror("Printer Storage", f"Unable to save printers: {error}")

    def _refreshPrinterList(self) -> None:
        if not hasattr(self, "printerTree"):
            return
        for itemId in self.printerTree.get_children():
            self.printerTree.delete(itemId)
        searchTerm = self.printerSearchVar.get().strip().lower()
        defaultTransport = getattr(self, "defaultConnectionMethod", "octoprint")
        for index, printer in enumerate(self.printers):
            nickname = printer.get("nickname", "")
            ipAddress = printer.get("ipAddress", "")
            if searchTerm and searchTerm not in nickname.lower() and searchTerm not in ipAddress.lower():
                continue
            nozzleTempDisplay = self._formatTemperature(self._parseOptionalFloat(printer.get("nozzleTemp")))
            bedTempDisplay = self._formatTemperature(self._parseOptionalFloat(printer.get("bedTemp")))
            progressDisplay = self._formatProgress(
                self._parseOptionalFloat(printer.get("progressPercent")),
                self._parseOptionalInt(printer.get("remainingTimeSeconds")),
                self._parseOptionalString(printer.get("gcodeState")),
            )
            connectionDisplay = str(
                printer.get("transport")
                or printer.get("connectionMethod")
                or defaultTransport
            )

            self.printerTree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    nickname,
                    ipAddress,
                    printer.get("accessCode", ""),
                    printer.get("serialNumber", ""),
                    printer.get("brand", ""),
                    printer.get("bambuModel", ""),
                    connectionDisplay,
                    printer.get("status", "Unknown"),
                    nozzleTempDisplay,
                    bedTempDisplay,
                    progressDisplay,
                ),
            )
        self._onPrinterSelection(None)

    def _applyBase44Environment(self) -> None:
        recipientValue = self.listenerRecipientId.strip()
        if recipientValue:
            os.environ["BASE44_RECIPIENT_ID"] = recipientValue
            self._managedEnvKeys.add("BASE44_RECIPIENT_ID")
        elif "BASE44_RECIPIENT_ID" in self._managedEnvKeys:
            os.environ.pop("BASE44_RECIPIENT_ID", None)
            self._managedEnvKeys.discard("BASE44_RECIPIENT_ID")

        apiKeyValue = self.listenerStatusApiKey.strip()
        if apiKeyValue:
            os.environ["BASE44_FUNCTIONS_API_KEY"] = apiKeyValue
            os.environ["BASE44_API_KEY"] = apiKeyValue
            self._managedEnvKeys.add("BASE44_FUNCTIONS_API_KEY")
            self._managedEnvKeys.add("BASE44_API_KEY")
        else:
            if "BASE44_FUNCTIONS_API_KEY" in self._managedEnvKeys:
                os.environ.pop("BASE44_FUNCTIONS_API_KEY", None)
                self._managedEnvKeys.discard("BASE44_FUNCTIONS_API_KEY")
            if "BASE44_API_KEY" in self._managedEnvKeys:
                os.environ.pop("BASE44_API_KEY", None)
                self._managedEnvKeys.discard("BASE44_API_KEY")

        controlKeyValue = self.listenerControlApiKey.strip()
        if controlKeyValue:
            os.environ["PRINTER_BACKEND_API_KEY"] = controlKeyValue
            self._managedEnvKeys.add("PRINTER_BACKEND_API_KEY")
        elif "PRINTER_BACKEND_API_KEY" in self._managedEnvKeys:
            os.environ.pop("PRINTER_BACKEND_API_KEY", None)
            self._managedEnvKeys.discard("PRINTER_BACKEND_API_KEY")

    def _updateListenerRecipient(self, *_args: Any) -> None:
        self.listenerRecipientId = self.recipientVar.get().strip() if hasattr(self, "recipientVar") else ""
        self._applyBase44Environment()

    def _updateListenerStatusApiKey(self, *_args: Any) -> None:
        self.listenerStatusApiKey = (
            self.statusApiKeyVar.get().strip() if hasattr(self, "statusApiKeyVar") else ""
        )
        self._applyBase44Environment()

    def _updateListenerControlApiKey(self, *_args: Any) -> None:
        self.listenerControlApiKey = (
            self.controlApiKeyVar.get().strip() if hasattr(self, "controlApiKeyVar") else ""
        )
        self._applyBase44Environment()

    def _updateStatusReporterState(self) -> None:
        listenerActive = bool(getattr(self, "listenerActive", False))
        listenerReady = bool(getattr(self, "listenerReady", False))
        recipientId = str(getattr(self, "listenerRecipientId", "") or "").strip()
        commandPoller = getattr(self, "commandPoller", None)
        reporter = getattr(self, "base44Reporter", None)

        def stopCommandPoller() -> None:
            if commandPoller is not None and hasattr(commandPoller, "stop"):
                commandPoller.stop()

        def startCommandPoller() -> None:
            if commandPoller is not None and hasattr(commandPoller, "start") and recipientId:
                commandPoller.start(recipientId)

        def stopReporter() -> None:
            if reporter is not None and hasattr(reporter, "stop") and getattr(self, "base44ReporterActive", False):
                reporter.stop()
                self.base44ReporterActive = False

        if not listenerActive or not listenerReady or not recipientId:
            stopReporter()
            stopCommandPoller()
            return

        snapshotCallable = getattr(self, "_snapshotPrintersForBase44", None)
        try:
            printerSnapshots = list(snapshotCallable()) if callable(snapshotCallable) else []
        except Exception:
            printerSnapshots = []

        hasMqttReadyPrinters = any(
            isinstance(entry, dict) and entry.get("mqttReady")
            for entry in printerSnapshots
        )

        if hasMqttReadyPrinters:
            if not getattr(self, "base44ReporterActive", False) and reporter is not None and hasattr(reporter, "start"):
                apiKeyResolver = getattr(self, "_resolveStatusApiKey", None)
                resolvedApiKey = apiKeyResolver() if callable(apiKeyResolver) else None
                reporter.start(recipientId, resolvedApiKey)
                self.base44ReporterActive = True
            stopCommandPoller()
        else:
            stopReporter()
            startCommandPoller()

    def _updateActivePrinterDialogIdentifiers(self) -> None:
        dialogInfo = getattr(self, "activePrinterDialog", None)
        if not isinstance(dialogInfo, dict):
            return
        variableMap = dialogInfo.get("vars", {})
        identifiers: set[str] = set()
        for key in ("serialNumber", "nickname", "ipAddress"):
            variable = variableMap.get(key)
            if isinstance(variable, tk.StringVar):
                normalized = variable.get().strip().lower()
                if normalized:
                    identifiers.add(normalized)
        dialogInfo["identifiers"] = identifiers

    def _handlePrintersConfigChange(self, updatedRecord: Dict[str, Any], _storagePath: Path) -> None:
        def refreshUi() -> None:
            self.printers = self._loadPrinters()
            self._refreshPrinterList()
            self._maybeRefreshActivePrinterDialog(updatedRecord)
            if self.liveStatusEnabledVar.get() and self.listenerThread and self.listenerThread.is_alive():
                self._startStatusSubscribers()

        try:
            self.root.after(0, refreshUi)
        except Exception:
            logging.exception("Failed to refresh printers after configuration update")

    def _maybeRefreshActivePrinterDialog(self, updatedRecord: Optional[Dict[str, Any]]) -> None:
        if not updatedRecord:
            return
        dialogInfo = getattr(self, "activePrinterDialog", None)
        if not isinstance(dialogInfo, dict) or not dialogInfo.get("isEdit"):
            return
        dialog = dialogInfo.get("dialog")
        if dialog is None or not dialog.winfo_exists():
            self.activePrinterDialog = None
            return

        recordIdentifiers: set[str] = set()
        for fieldName in ("serialNumber", "nickname", "ipAddress"):
            value = updatedRecord.get(fieldName)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized:
                    recordIdentifiers.add(normalized)

        if not recordIdentifiers:
            return

        dialogIdentifiers = set(dialogInfo.get("identifiers") or [])
        if not dialogIdentifiers:
            self._updateActivePrinterDialogIdentifiers()
            dialogIdentifiers = set(dialogInfo.get("identifiers") or [])

        if not dialogIdentifiers.intersection(recordIdentifiers):
            return

        self._populateActivePrinterDialog(dict(updatedRecord))

    def _populateActivePrinterDialog(self, printerRecord: Dict[str, Any]) -> None:
        dialogInfo = getattr(self, "activePrinterDialog", None)
        if not isinstance(dialogInfo, dict):
            return
        dialog = dialogInfo.get("dialog")
        if dialog is None or not dialog.winfo_exists():
            self.activePrinterDialog = None
            return

        variableMap = dialogInfo.get("vars", {})
        recordCopy = dict(printerRecord)
        sanitizedRecord = self._applyTelemetryDefaults(recordCopy)

        mapping = {
            "nickname": sanitizedRecord.get("nickname", ""),
            "ipAddress": sanitizedRecord.get("ipAddress", ""),
            "accessCode": sanitizedRecord.get("accessCode", ""),
            "serialNumber": sanitizedRecord.get("serialNumber", ""),
            "brand": sanitizedRecord.get("brand", ""),
            "bambuModel": sanitizedRecord.get("bambuModel", ""),
        }

        for key, value in mapping.items():
            variable = variableMap.get(key)
            if isinstance(variable, tk.StringVar):
                variable.set(str(value or ""))

        connectionVariable = variableMap.get("connectionMethod")
        resolvedConnection = str(
            sanitizedRecord.get("transport")
            or sanitizedRecord.get("connectionMethod")
            or ""
        )
        if resolvedConnection.lower() == "lan":
            resolvedConnection = getattr(self, "mqttConnectionMethod", "mqtt")
        if isinstance(connectionVariable, tk.StringVar):
            connectionVariable.set(resolvedConnection)

        accessCodeVariable = variableMap.get("accessCode")
        if isinstance(accessCodeVariable, tk.StringVar):
            accessCodeVariable.set(str(mapping["accessCode"]))

        updateControls = dialogInfo.get("updateControls")
        if callable(updateControls):
            updateControls()

        dialogInfo["transport"] = sanitizedRecord.get("transport")
        dialogInfo["useCloud"] = sanitizedRecord.get("useCloud")
        self._updateActivePrinterDialogIdentifiers()

    def _clearPrinterSearch(self) -> None:
        self.printerSearchVar.set("")

    def _openAddPrinterDialog(self) -> None:
        self._showPrinterDialog(
            title="Add 3D Printer",
            initialValues=None,
            onSave=self._handleCreatePrinter,
        )

    def _openEditPrinterDialog(self) -> None:
        selectedIndex = self._getSelectedPrinterIndex()
        if selectedIndex is None:
            return
        self._showPrinterDialog(
            title="Edit 3D Printer",
            initialValues=self.printers[selectedIndex],
            onSave=lambda updated: self._handleUpdatePrinter(selectedIndex, updated),
        )

    def openManualStatusDialog(self) -> None:
        selectedIndex = self._getSelectedPrinterIndex()
        if selectedIndex is None:
            messagebox.showinfo("Printer Status", "Please select a printer first.")
            return

        printer = self.printers[selectedIndex]
        manualDefaults = printer.get("manualStatusDefaults")
        if not isinstance(manualDefaults, dict):
            manualDefaults = {}

        dialog = tk.Toplevel(self.root)
        dialog.title("Send Test Printer Status")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.columnconfigure(0, weight=1)

        baseUrlDefault = str(
            getPrinterStatusEndpointUrl() or self.baseUrlVar.get() or ""
        )
        apiKeyDefault = (
            self.statusApiKeyVar.get().strip() if hasattr(self, "statusApiKeyVar") else ""
        )
        recipientDefault = (
            self.recipientVar.get().strip() if hasattr(self, "recipientVar") else ""
        )

        baseUrlVar = tk.StringVar(value=baseUrlDefault)
        apiKeyVar = tk.StringVar(value=apiKeyDefault)
        recipientVar = tk.StringVar(value=str(recipientDefault or ""))
        printerIpVar = tk.StringVar(value=str(printer.get("ipAddress", "")))
        serialVar = tk.StringVar(value=str(printer.get("serialNumber", "")))
        publicKeyVar = tk.StringVar(value=self._formatOptionalNumber(manualDefaults.get("publicKey")))
        accessCodeVar = tk.StringVar(value=str(printer.get("accessCode", "")))
        objectNameVar = tk.StringVar(value=self._formatOptionalNumber(manualDefaults.get("objectName")))
        productNameVar = tk.StringVar(value=self._formatOptionalNumber(manualDefaults.get("productName")))
        printJobIdInitial = manualDefaults.get("printJobId") or str(uuid.uuid4())
        printJobIdVar = tk.StringVar(value=self._formatOptionalNumber(printJobIdInitial))
        manualUseAms = manualDefaults.get("useAms")
        if manualUseAms is True:
            initialUseAms = "True"
        elif manualUseAms is False:
            initialUseAms = "False"
        else:
            initialUseAms = "Auto"
        useAmsVar = tk.StringVar(value=initialUseAms)
        platesRequestedValue = manualDefaults.get("platesRequested") if manualDefaults.get("platesRequested") else 1
        platesRequestedVar = tk.StringVar(value=self._formatOptionalNumber(platesRequestedValue))
        statusDefault = manualDefaults.get("status") or str(printer.get("status", "idle")) or "idle"
        statusVar = tk.StringVar(value=str(statusDefault).strip())
        jobProgressVar = tk.StringVar(value=self._formatOptionalNumber(manualDefaults.get("jobProgress")))
        nozzleTempVar = tk.StringVar(value=self._formatOptionalNumber(manualDefaults.get("nozzleTemp")))
        bedTempVar = tk.StringVar(value=self._formatOptionalNumber(manualDefaults.get("bedTemp")))
        materialLevelVar = tk.StringVar(value=self._formatMaterialLevelForEntry(manualDefaults.get("materialLevel")))
        lastUpdateTimestampDefault = str(
            manualDefaults.get("lastUpdateTimestamp")
            or datetime.utcnow().isoformat(timespec="seconds") + "Z"
        )
        lastUpdateTimestampVar = tk.StringVar(value=lastUpdateTimestampDefault)
        statusMessageVar = tk.StringVar(value="")

        if not publicKeyVar.get():
            publicKeyVar.set("MANUAL-KEY")
        if not objectNameVar.get():
            objectNameVar.set("manual_test_object")
        if not productNameVar.get():
            productNameVar.set("manual_product")
        if not statusVar.get():
            statusVar.set("idle")
        if not platesRequestedVar.get():
            platesRequestedVar.set("1")
        if not jobProgressVar.get():
            jobProgressVar.set("0")
        if not materialLevelVar.get():
            materialLevelVar.set(json.dumps({"filamentA": 100}, ensure_ascii=False))

        connectionFrame = ttk.LabelFrame(dialog, text="Connection")
        connectionFrame.grid(row=0, column=0, sticky=tk.EW, padx=12, pady=(12, 6))
        connectionFrame.columnconfigure(1, weight=1)
        ttk.Label(connectionFrame, text="Base URL:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(connectionFrame, textvariable=baseUrlVar, width=40, state="readonly").grid(
            row=0, column=1, sticky=tk.EW, padx=6, pady=4
        )
        ttk.Label(connectionFrame, text="API Key:").grid(row=1, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(
            connectionFrame, textvariable=apiKeyVar, show="*", width=40, state="readonly"
        ).grid(
            row=1, column=1, sticky=tk.EW, padx=6, pady=4
        )
        ttk.Label(connectionFrame, text="Recipient ID:").grid(row=2, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(connectionFrame, textvariable=recipientVar, width=40, state="readonly").grid(
            row=2, column=1, sticky=tk.EW, padx=6, pady=4
        )

        identityFrame = ttk.LabelFrame(dialog, text="Printer Identity")
        identityFrame.grid(row=1, column=0, sticky=tk.EW, padx=12, pady=6)
        identityFrame.columnconfigure(1, weight=1)
        ttk.Label(identityFrame, text="Printer IP:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(identityFrame, textvariable=printerIpVar, width=30).grid(
            row=0, column=1, sticky=tk.EW, padx=6, pady=4
        )
        ttk.Label(identityFrame, text="Serial Number:").grid(row=1, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(identityFrame, textvariable=serialVar, width=30).grid(
            row=1, column=1, sticky=tk.EW, padx=6, pady=4
        )
        ttk.Label(identityFrame, text="Public Key:").grid(row=2, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(identityFrame, textvariable=publicKeyVar, width=30).grid(
            row=2, column=1, sticky=tk.EW, padx=6, pady=4
        )
        ttk.Label(identityFrame, text="Access Code:").grid(row=3, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(identityFrame, textvariable=accessCodeVar, width=30).grid(
            row=3, column=1, sticky=tk.EW, padx=6, pady=4
        )

        jobFrame = ttk.LabelFrame(dialog, text="Job Details")
        jobFrame.grid(row=2, column=0, sticky=tk.EW, padx=12, pady=6)
        jobFrame.columnconfigure(1, weight=1)
        ttk.Label(jobFrame, text="Object Name:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(jobFrame, textvariable=objectNameVar).grid(row=0, column=1, sticky=tk.EW, padx=6, pady=4)
        ttk.Label(jobFrame, text="Product Name:").grid(row=1, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(jobFrame, textvariable=productNameVar).grid(row=1, column=1, sticky=tk.EW, padx=6, pady=4)
        ttk.Label(jobFrame, text="Print Job ID:").grid(row=2, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(jobFrame, textvariable=printJobIdVar).grid(row=2, column=1, sticky=tk.EW, padx=6, pady=4)
        ttk.Label(jobFrame, text="Use AMS:").grid(row=3, column=0, sticky=tk.W, padx=6, pady=4)
        useAmsOptions = ("Auto", "True", "False")
        useAmsCombo = ttk.Combobox(jobFrame, textvariable=useAmsVar, values=useAmsOptions, state="readonly", width=8)
        useAmsCombo.grid(row=3, column=1, sticky=tk.W, padx=6, pady=4)
        useAmsHelp = ttk.Label(
            jobFrame,
            text=(
                "Auto velger AMS når jobben krever AMS, ellers spole. "
                "Hvis skriver sier ‘trekk ut filament’ for AMS-jobb, forsøker vi automatisk på nytt med AMS av."
            ),
            wraplength=360,
        )
        useAmsHelp.grid(row=4, column=0, columnspan=3, sticky=tk.W, padx=6, pady=(0, 6))
        useAmsHelp.configure(foreground="gray")
        ttk.Label(jobFrame, text="Plates Requested:").grid(row=5, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(jobFrame, textvariable=platesRequestedVar, width=10).grid(
            row=5, column=1, sticky=tk.W, padx=6, pady=4
        )
        ttk.Label(jobFrame, text="Material Level (JSON):").grid(row=6, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(jobFrame, textvariable=materialLevelVar, width=48).grid(
            row=6, column=1, columnspan=2, sticky=tk.EW, padx=6, pady=4
        )

        telemetryFrame = ttk.LabelFrame(dialog, text="Telemetry Overrides")
        telemetryFrame.grid(row=3, column=0, sticky=tk.EW, padx=12, pady=6)
        telemetryFrame.columnconfigure(1, weight=1)
        statusOptions = ["idle", "printing", "paused", "pausing", "error", "finished", "completed", "offline"]
        ttk.Label(telemetryFrame, text="Status:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        statusCombo = ttk.Combobox(telemetryFrame, textvariable=statusVar, values=statusOptions)
        statusCombo.grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(telemetryFrame, text="Last Update Timestamp:").grid(
            row=1, column=0, sticky=tk.W, padx=6, pady=4
        )
        ttk.Entry(telemetryFrame, textvariable=lastUpdateTimestampVar, width=24).grid(
            row=1, column=1, sticky=tk.W, padx=6, pady=4
        )
        ttk.Label(telemetryFrame, text="Job Progress (%):").grid(row=2, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(telemetryFrame, textvariable=jobProgressVar, width=10).grid(
            row=2, column=1, sticky=tk.W, padx=6, pady=4
        )
        ttk.Label(telemetryFrame, text="Nozzle Temp (°C):").grid(row=3, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(telemetryFrame, textvariable=nozzleTempVar, width=10).grid(
            row=3, column=1, sticky=tk.W, padx=6, pady=4
        )
        ttk.Label(telemetryFrame, text="Bed Temp (°C):").grid(row=4, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(telemetryFrame, textvariable=bedTempVar, width=10).grid(
            row=4, column=1, sticky=tk.W, padx=6, pady=4
        )

        buttonFrame = ttk.Frame(dialog)
        buttonFrame.grid(row=4, column=0, pady=8)

        def buildRequestData() -> Dict[str, Any]:
            baseUrlRaw = baseUrlVar.get().strip()
            if not baseUrlRaw:
                raise ValueError("Base URL is required.")
            statusEndpointUrl = getPrinterStatusEndpointUrl()
            if baseUrlRaw and baseUrlRaw != statusEndpointUrl:
                try:
                    buildBaseUrl(baseUrlRaw)
                except ValueError as error:
                    raise ValueError(f"Invalid base URL: {error}") from error
            baseUrlVar.set(statusEndpointUrl)

            apiKey = apiKeyVar.get().strip()
            if not apiKey:
                raise ValueError("API key is required.")

            printerIpAddress = printerIpVar.get().strip()
            if not printerIpAddress:
                raise ValueError("Printer IP address is required.")

            printerSerial = serialVar.get().strip()

            publicKey = publicKeyVar.get().strip()
            if not publicKey:
                raise ValueError("Public key is required.")

            accessCode = accessCodeVar.get().strip()

            objectName = objectNameVar.get().strip()
            if not objectName:
                raise ValueError("Object name is required.")

            productName = productNameVar.get().strip()
            if not productName:
                raise ValueError("Product name is required.")

            printJobId = printJobIdVar.get().strip() or str(uuid.uuid4())

            selectedUseAms = useAmsVar.get().strip().lower()
            if selectedUseAms == "true":
                useAms: Optional[bool] = True
            elif selectedUseAms == "false":
                useAms = False
            else:
                useAms = None

            platesRequested = self._parseOptionalInt(platesRequestedVar.get())
            if platesRequested is None or platesRequested <= 0:
                raise ValueError("Plates requested must be a positive integer.")

            materialLevelText = materialLevelVar.get().strip()
            if materialLevelText:
                try:
                    materialLevel = json.loads(materialLevelText)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Material level must be valid JSON: {error.msg}") from error
                if not isinstance(materialLevel, dict):
                    raise ValueError("Material level JSON must describe an object.")
            else:
                materialLevel = {}

            statusValue = statusVar.get().strip()
            if not statusValue:
                raise ValueError("Status is required.")

            jobProgress = self._parseOptionalFloat(jobProgressVar.get())
            if jobProgress is None or jobProgress < 0:
                raise ValueError("Job progress must be a non-negative number.")
            jobProgressValue = float(jobProgress)

            lastUpdateTimestamp = lastUpdateTimestampVar.get().strip()
            if not lastUpdateTimestamp:
                lastUpdateTimestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                lastUpdateTimestampVar.set(lastUpdateTimestamp)
            else:
                try:
                    datetime.strptime(lastUpdateTimestamp, "%Y-%m-%dT%H:%M:%SZ")
                except ValueError as error:
                    raise ValueError(
                        "Last update timestamp must be in ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ)."
                    ) from error

            nozzleTempValue = self._parseOptionalFloat(nozzleTempVar.get())
            bedTempValue = self._parseOptionalFloat(bedTempVar.get())

            recipientId = recipientVar.get().strip()
            if recipientId == "":
                recipientIdValue: Optional[str] = None
            else:
                recipientIdValue = recipientId

            payload: Dict[str, Any] = {
                "printerIpAddress": printerIpAddress,
                "publicKey": publicKey,
                "objectName": objectName,
                "useAms": useAms,
                "printJobId": printJobId,
                "productName": productName,
                "platesRequested": platesRequested,
                "status": statusValue,
                "jobProgress": jobProgressValue,
                "materialLevel": materialLevel,
                "lastUpdateTimestamp": lastUpdateTimestamp,
            }

            payload = addPrinterIdentityToPayload(payload, printerSerial, accessCode)
            if recipientIdValue:
                payload["recipientId"] = recipientIdValue
            if nozzleTempValue is not None:
                payload["nozzleTemp"] = nozzleTempValue
            if bedTempValue is not None:
                payload["bedTemp"] = bedTempValue

            headers = {"X-API-Key": apiKey, "Content-Type": "application/json"}
            statusUrl = statusEndpointUrl

            manualDefaultsUpdate: Dict[str, Any] = {
                "publicKey": publicKey,
                "objectName": objectName,
                "productName": productName,
                "printJobId": printJobId,
                "useAms": useAms,
                "platesRequested": platesRequested,
                "materialLevel": materialLevel,
                "status": statusValue,
                "jobProgress": jobProgressValue,
                "lastUpdateTimestamp": lastUpdateTimestamp,
            }
            if nozzleTempValue is not None:
                manualDefaultsUpdate["nozzleTemp"] = nozzleTempValue
            if bedTempValue is not None:
                manualDefaultsUpdate["bedTemp"] = bedTempValue

            displayStatus = statusValue.title() if statusValue.islower() else statusValue

            return {
                "url": statusUrl,
                "headers": headers,
                "payload": payload,
                "baseUrl": statusEndpointUrl,
                "apiKey": apiKey,
                "recipientId": recipientIdValue,
                "manualDefaults": manualDefaultsUpdate,
                "displayStatus": displayStatus,
                "jobProgress": jobProgressValue,
                "nozzleTemp": nozzleTempValue,
                "bedTemp": bedTempValue,
                "printerSerial": printerSerial or None,
                "accessCode": accessCode or None,
            }

        def finalizeSend(success: bool, message: str, requestData: Optional[Dict[str, Any]]) -> None:
            if success and requestData is not None:
                printerRecord = dict(printer)
                printerRecord["manualStatusDefaults"] = requestData["manualDefaults"]
                displayStatus = requestData.get("displayStatus")
                if displayStatus:
                    printerRecord["status"] = displayStatus
                jobProgressValue = requestData.get("jobProgress")
                if isinstance(jobProgressValue, (int, float)):
                    printerRecord["progressPercent"] = jobProgressValue
                nozzleTempValue = requestData.get("nozzleTemp")
                if nozzleTempValue is not None:
                    printerRecord["nozzleTemp"] = nozzleTempValue
                bedTempValue = requestData.get("bedTemp")
                if bedTempValue is not None:
                    printerRecord["bedTemp"] = bedTempValue
                self.printers[selectedIndex] = self._applyTelemetryDefaults(printerRecord)
                self._savePrinters()
                self._refreshPrinterList()
                statusMessageVar.set("Status update sent successfully.")
                messagebox.showinfo(
                    "Printer Status",
                    f"Status sent successfully.\n{message or 'Printer status updated.'}",
                    parent=dialog,
                )
                dialog.destroy()
            else:
                failureMessage = message or "Unknown error"
                statusMessageVar.set(f"Failed to send status: {failureMessage}")
                messagebox.showerror(
                    "Printer Status",
                    f"Failed to send status update.\n{failureMessage}",
                    parent=dialog,
                )
                sendButton.config(state=tk.NORMAL)
                cancelButton.config(state=tk.NORMAL)

        def handleSend() -> None:
            try:
                requestData = buildRequestData()
            except ValueError as error:
                messagebox.showerror("Printer Status", str(error), parent=dialog)
                return

            statusMessageVar.set("Sending status update...")
            sendButton.config(state=tk.DISABLED)
            cancelButton.config(state=tk.DISABLED)

            def worker() -> None:
                try:
                    response = requests.post(
                        requestData["url"],
                        headers=requestData["headers"],
                        json=requestData["payload"],
                        timeout=30,
                    )
                    response.raise_for_status()
                    responseText = response.text.strip() or f"{response.status_code} {response.reason}"
                    self.root.after(0, lambda: finalizeSend(True, responseText, requestData))
                except requests.RequestException as requestError:
                    errorMessage = str(requestError)
                    self.root.after(0, lambda: finalizeSend(False, errorMessage, requestData))

            threading.Thread(target=worker, daemon=True).start()

        sendButton = ttk.Button(buttonFrame, text="Send Status", command=handleSend)
        sendButton.pack(side=tk.LEFT, padx=6)
        cancelButton = ttk.Button(buttonFrame, text="Cancel", command=dialog.destroy)
        cancelButton.pack(side=tk.LEFT, padx=6)

        ttk.Label(dialog, textvariable=statusMessageVar, foreground="gray").grid(
            row=5, column=0, padx=12, pady=(0, 12), sticky=tk.W
        )

        dialog.wait_window(dialog)

    def _showPrinterDialog(
        self,
        *,
        title: str,
        initialValues: Optional[Dict[str, Any]],
        onSave: Callable[[Dict[str, Any]], None],
    ) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()

        for index in range(2):
            dialog.columnconfigure(index, weight=1)

        nicknameVar = tk.StringVar(value=(initialValues or {}).get("nickname", ""))
        ipAddressVar = tk.StringVar(value=(initialValues or {}).get("ipAddress", ""))
        accessCodeVar = tk.StringVar(value=(initialValues or {}).get("accessCode", ""))
        serialNumberVar = tk.StringVar(value=(initialValues or {}).get("serialNumber", ""))
        brandVar = tk.StringVar(value=(initialValues or {}).get("brand", ""))
        bambuModelVar = tk.StringVar(value=(initialValues or {}).get("bambuModel", ""))
        connectionMethodVar = tk.StringVar(value=(initialValues or {}).get("connectionMethod", ""))
        initialStatus = (initialValues or {}).get("status", "Unknown") or "Unknown"

        ttk.Label(dialog, text="Nickname:").grid(row=0, column=0, sticky=tk.W, padx=12, pady=(12, 4))
        ttk.Entry(dialog, textvariable=nicknameVar).grid(row=0, column=1, sticky=tk.EW, padx=12, pady=(12, 4))

        ttk.Label(dialog, text="IP Address:").grid(row=1, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=ipAddressVar).grid(row=1, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Access Code:").grid(row=2, column=0, sticky=tk.W, padx=12, pady=4)
        accessCodeEntry = ttk.Entry(dialog, textvariable=accessCodeVar)
        accessCodeEntry.grid(row=2, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Serial Number:").grid(row=3, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=serialNumberVar).grid(row=3, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Brand:").grid(row=4, column=0, sticky=tk.W, padx=12, pady=4)
        brandCombo = ttk.Combobox(
            dialog,
            textvariable=brandVar,
            values=("", *self.printerBrandOptions),
        )
        brandCombo.grid(row=4, column=1, sticky=tk.EW, padx=12, pady=4)
        brandCombo.configure(state="readonly")

        ttk.Label(dialog, text="Bambu Model:").grid(row=5, column=0, sticky=tk.W, padx=12, pady=4)
        bambuModelCombo = ttk.Combobox(
            dialog,
            textvariable=bambuModelVar,
            values=("", *self.bambuModelOptions),
            state="readonly",
        )
        bambuModelCombo.grid(row=5, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Connection Method:").grid(row=6, column=0, sticky=tk.W, padx=12, pady=4)
        connectionMethodCombo = ttk.Combobox(
            dialog,
            textvariable=connectionMethodVar,
            state="readonly",
        )
        connectionMethodCombo.grid(row=6, column=1, sticky=tk.EW, padx=12, pady=4)

        bambuConnect = getattr(self, "bambuConnectMethod", "bambu_connect")
        defaultTransport = getattr(self, "defaultConnectionMethod", "octoprint")
        mqttTransport = getattr(self, "mqttConnectionMethod", "mqtt")

        def updateConnectionControls(*_args: Any) -> None:
            brandValue = brandVar.get().strip()
            normalizedBrand = brandValue.lower()
            isBambuBrand = bool(normalizedBrand and "bambu" in normalizedBrand)

            bambuOptionsMap = getattr(self, "bambuModelCanonicalMap", {})
            bambuOptions = getattr(self, "bambuModelOptions", [])
            if not bambuOptionsMap and bambuOptions:
                bambuOptionsMap = {model.lower(): model for model in bambuOptions}

            def updateAccessCodeState() -> None:
                resolvedConnection = connectionMethodVar.get().strip().lower()
                if resolvedConnection == str(bambuConnect).lower():
                    if accessCodeVar.get():
                        accessCodeVar.set("")
                    accessCodeEntry.configure(state=tk.DISABLED)
                else:
                    accessCodeEntry.configure(state=tk.NORMAL)

            currentModel = bambuModelVar.get().strip()
            canonicalModel = bambuOptionsMap.get(currentModel.lower(), currentModel)
            if canonicalModel and canonicalModel != currentModel:
                bambuModelVar.set(canonicalModel)
                currentModel = canonicalModel

            if not isBambuBrand:
                if currentModel:
                    bambuModelVar.set("")
                bambuModelCombo.configure(state="disabled")
                connectionMethodCombo.configure(state="readonly")
                availableTransports = (defaultTransport, mqttTransport)
                connectionMethodCombo.config(values=availableTransports)
                if connectionMethodVar.get().strip().lower() not in {
                    transport.lower() for transport in availableTransports
                }:
                    connectionMethodVar.set(defaultTransport)
                updateAccessCodeState()
                return

            bambuModelCombo.configure(state="readonly")
            if not currentModel and bambuOptions:
                bambuModelVar.set(bambuOptions[0])
                currentModel = bambuOptions[0]

            supportedModels = {key.lower() for key in bambuOptionsMap.keys()}
            isSupportedModel = bool(currentModel and currentModel.lower() in supportedModels)

            normalizedConnection = connectionMethodVar.get().strip().lower()
            if normalizedConnection == "legacy":
                connectionMethodVar.set(bambuConnect)
                normalizedConnection = bambuConnect
            if normalizedConnection == "lan":
                connectionMethodVar.set(mqttTransport)
                normalizedConnection = mqttTransport

            if not isSupportedModel:
                connectionMethodCombo.configure(state="readonly")
                connectionMethodCombo.config(values=(mqttTransport,))
                if normalizedConnection != mqttTransport:
                    connectionMethodVar.set(mqttTransport)
                updateAccessCodeState()
                return

            connectionMethodCombo.configure(state="readonly")
            availableTransports = (bambuConnect, mqttTransport)
            connectionMethodCombo.config(values=availableTransports)
            if normalizedConnection not in {transport.lower() for transport in availableTransports}:
                connectionMethodVar.set(bambuConnect)
            updateAccessCodeState()

        brandVar.trace_add("write", updateConnectionControls)
        bambuModelVar.trace_add("write", updateConnectionControls)
        connectionMethodVar.trace_add("write", lambda *_: updateConnectionControls())
        initialTransports = tuple(
            getattr(self, "connectionMethodOptions", [defaultTransport, mqttTransport, bambuConnect])
        )
        connectionMethodCombo.config(values=initialTransports)
        updateConnectionControls()

        def handleDialogDestroyed(event: Any) -> None:
            if event.widget is dialog and isinstance(getattr(self, "activePrinterDialog", None), dict):
                if self.activePrinterDialog.get("dialog") is dialog:
                    self.activePrinterDialog = None

        dialog.bind("<Destroy>", handleDialogDestroyed)

        self.activePrinterDialog = {
            "dialog": dialog,
            "vars": {
                "nickname": nicknameVar,
                "ipAddress": ipAddressVar,
                "accessCode": accessCodeVar,
                "serialNumber": serialNumberVar,
                "brand": brandVar,
                "bambuModel": bambuModelVar,
                "connectionMethod": connectionMethodVar,
            },
            "accessEntry": accessCodeEntry,
            "updateControls": updateConnectionControls,
            "isEdit": bool(initialValues),
            "transport": (initialValues or {}).get("transport"),
            "useCloud": (initialValues or {}).get("useCloud"),
        }
        self._updateActivePrinterDialogIdentifiers()

        for trackedVar in (nicknameVar, ipAddressVar, serialNumberVar):
            trackedVar.trace_add("write", lambda *_: self._updateActivePrinterDialogIdentifiers())

        ttk.Label(
            dialog,
            text="Recipient, API key og URL styres fra Listener-panelet.",
        ).grid(row=7, column=0, columnspan=2, sticky=tk.W, padx=12, pady=(0, 4))
        statusLabelRow = 8
        statusInfoLabelRow = 9

        ttk.Label(dialog, text=f"Status: {initialStatus}").grid(
            row=statusLabelRow,
            column=0,
            columnspan=2,
            sticky=tk.W,
            padx=12,
            pady=4,
        )

        statusInfoLabel = ttk.Label(
            dialog,
            text="Status is updated automatically based on telemetry.",
        )
        statusInfoLabel.grid(
            row=statusInfoLabelRow,
            column=0,
            columnspan=2,
            sticky=tk.W,
            padx=12,
            pady=(0, 4),
        )
        statusInfoLabel.configure(foreground="gray")

        buttonFrame = ttk.Frame(dialog)
        buttonFrame.grid(row=statusInfoLabelRow + 1, column=0, columnspan=2, pady=12)
        ttk.Button(
            buttonFrame,
            text="Save",
            command=lambda: self._handlePrinterDialogSave(
                dialog,
                nicknameVar,
                ipAddressVar,
                accessCodeVar,
                serialNumberVar,
                brandVar,
                bambuModelVar,
                connectionMethodVar,
                onSave,
            ),
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttonFrame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=6)

        dialog.wait_window(dialog)

    def _handlePrinterDialogSave(
        self,
        dialog: tk.Toplevel,
        nicknameVar: tk.StringVar,
        ipAddressVar: tk.StringVar,
        accessCodeVar: tk.StringVar,
        serialNumberVar: tk.StringVar,
        brandVar: tk.StringVar,
        bambuModelVar: tk.StringVar,
        connectionMethodVar: tk.StringVar,
        onSave: Callable[[Dict[str, Any]], None],
    ) -> None:
        nickname = nicknameVar.get().strip()
        ipAddress = ipAddressVar.get().strip()
        serialNumber = serialNumberVar.get().strip()
        brand = brandVar.get().strip()
        bambuModel = bambuModelVar.get().strip()
        connectionMethod = connectionMethodVar.get().strip().lower()
        accessCode = (
            ""
            if connectionMethod == getattr(self, "bambuConnectMethod", "bambu_connect")
            else accessCodeVar.get().strip()
        )

        if not nickname or not ipAddress:
            messagebox.showerror(
                "Printer Details",
                "Nickname and IP address are required.",
                parent=dialog,
            )
            dialog.lift()
            return

        printerDetails = {
            "nickname": nickname,
            "ipAddress": ipAddress,
            "accessCode": accessCode,
            "serialNumber": serialNumber,
            "brand": brand,
            "bambuModel": bambuModel,
            "connectionMethod": connectionMethod,
        }

        bambuConnect = getattr(self, "bambuConnectMethod", "bambu_connect")
        mqttTransport = getattr(self, "mqttConnectionMethod", "mqtt")

        if connectionMethod == bambuConnect:
            printerDetails["connectionMethod"] = "bambu_connect"
            printerDetails["transport"] = "bambu_connect"
            printerDetails["useCloud"] = True
        elif connectionMethod in {mqttTransport, "lan"}:
            printerDetails["connectionMethod"] = "lan"
            printerDetails["transport"] = "lan"
            printerDetails["useCloud"] = False

        onSave(self._applyTelemetryDefaults(printerDetails))
        dialog.destroy()

    def _handleCreatePrinter(self, printerDetails: Dict[str, Any]) -> None:
        self.printers.append(self._applyTelemetryDefaults(dict(printerDetails)))
        self._savePrinters()
        self._refreshPrinterList()
        self._scheduleStatusRefresh(0)

    def _handleUpdatePrinter(self, index: int, printerDetails: Dict[str, Any]) -> None:
        existing = dict(self.printers[index]) if 0 <= index < len(self.printers) else {}
        existing.update(printerDetails)
        self.printers[index] = self._applyTelemetryDefaults(existing)
        self._savePrinters()
        self._refreshPrinterList()
        self._scheduleStatusRefresh(0)

    def _onPrinterSelection(self, event: object) -> None:  # noqa: ARG002 - required by Tk callback
        state = tk.NORMAL if self._getSelectedPrinterIndex() is not None else tk.DISABLED
        self.editPrinterButton.config(state=state)
        if hasattr(self, "sendTestStatusButton"):
            self.sendTestStatusButton.config(state=state)
        if hasattr(self, "captureReferenceButton"):
            self.captureReferenceButton.config(state=state)
        if hasattr(self, "runBrakeDemoButton"):
            self.runBrakeDemoButton.config(state=state)

    def _captureSelectedBedReference(self) -> None:
        """
        Starter bed reference capture for valgt printer.

        Prosess:
        1. Henter valgt printer fra GUI
        2. Validerer at printer har serial number
        3. Henter command worker for printeren
        4. Kjører bed reference capture i en separat tråd
        5. Logger resultat til GUI
        """
        # Hent valgt printer
        index = self._getSelectedPrinterIndex()
        if index is None:
            self.log("Ingen printer valgt. Velg en printer først.")
            return

        printer = self.printers[index]
        serial = str(printer.get("serialNumber") or "").strip()
        if not serial:
            self.log("Kan ikke starte bed reference capture - printer mangler serial number.")
            return

        # Hent command worker
        worker = self.commandWorkers.get(serial)
        if worker is None:
            self.log(f"Ingen aktiv command worker for {serial}. Koble til printer først.")
            return

        self.log(f"Starter bed reference capture for {serial}...")

        def task() -> None:
            """Kjører bed reference capture i bakgrunnen"""
            try:
                # Kjør capture med standard verdier: 5mm steg, 200mm total
                frames = worker.captureBedReference(zStepMm=5.0, totalMm=200.0)
                self.log(f"Bed reference capture fullført for {serial} - {len(frames)} bilder lagret")

                # Vis hvor bildene ble lagret
                if frames:
                    bedRefDir = frames[0].parent
                    self.log(f"Bilder lagret i: {bedRefDir}")

            except Exception as error:
                self.log(f"Bed reference capture feilet for {serial}: {error}")

        # Start capture i separat tråd for å ikke blokkere GUI
        threading.Thread(
            target=task,
            name=f"CaptureReference-{serial}",
            daemon=True
        ).start()

    def _runBrakeDemoForSelected(self) -> None:
        index = self._getSelectedPrinterIndex()
        if index is None:
            return
        printer = self.printers[index]
        serial = str(printer.get("serialNumber") or "").strip()
        if not serial:
            self.log("Unable to run brake demo – printer is missing a serial number.")
            return
        worker = self.commandWorkers.get(serial)
        if worker is None:
            self.log(f"No active command worker for {serial}. Connect printers first.")
            return
        ipAddress = str(printer.get("ipAddress") or "").strip() or None
        context = BrakeFlowContext(
            serial=serial,
            ipAddress=ipAddress,
            jobKey="manual-demo",
            enableBrakePlate=True,
            platesRequested=2,
            checkpointPaths={},
            metadata={"source": "manual"},
        )

        def task() -> None:
            try:
                result = worker.runBrakeDemo(context)
            except Exception as error:
                self.log(f"Brake demo failed for {serial}: {error}")
            else:
                outcome = "clear" if result else "obstructed"
                self.log(f"Brake demo for {serial}: {outcome}")

        threading.Thread(target=task, name=f"BrakeDemo-{serial}", daemon=True).start()

    def refreshPrintersNow(self) -> None:
        if self.statusRefreshThread and self.statusRefreshThread.is_alive():
            self.pendingImmediateStatusRefresh = True
            return
        if hasattr(self, "connectPrintersButton"):
            self.connectPrintersButton.config(state=tk.DISABLED)
        self._scheduleStatusRefresh(0)

    def _getSelectedPrinterIndex(self) -> Optional[int]:
        selection = self.printerTree.selection() if hasattr(self, "printerTree") else ()
        if not selection:
            return None
        selectedId = selection[0]
        try:
            return int(selectedId)
        except (TypeError, ValueError):
            return None

    def _scheduleStatusRefresh(self, delayMs: int) -> None:
        if self.statusRefreshThread and self.statusRefreshThread.is_alive():
            if delayMs == 0:
                self.pendingImmediateStatusRefresh = True
            return
        self.pendingImmediateStatusRefresh = False
        self.root.after(delayMs, self._refreshPrinterStatusesAsync)

    def _refreshPrinterStatusesAsync(self) -> None:
        if self.statusRefreshThread and self.statusRefreshThread.is_alive():
            return
        worker = threading.Thread(target=self._refreshPrinterStatusesWorker, daemon=True)
        self.statusRefreshThread = worker
        worker.start()

    def _refreshPrinterStatusesWorker(self) -> None:
        updates: list[Dict[str, Any]] = []
        printersSnapshot = list(enumerate(list(self.printers)))
        for index, printer in printersSnapshot:
            ipAddress = str(printer.get("ipAddress", "")).strip()
            if not ipAddress:
                continue
            telemetry = self._collectPrinterTelemetry(printer)
            if not telemetry:
                continue
            pendingChanges: Dict[str, Any] = {}
            currentDetails = self.printers[index] if 0 <= index < len(self.printers) else {}
            for key, value in telemetry.items():
                if currentDetails.get(key) != value:
                    pendingChanges[key] = value
            if pendingChanges:
                if "status" in pendingChanges:
                    logging.info(
                        "Printer %s status changed from %s to %s",
                        ipAddress,
                        currentDetails.get("status", "Unknown"),
                        pendingChanges["status"],
                    )
                    if pendingChanges.get("status") == "Online":
                        try:
                            self._sendAutomaticPrinterStatus(index, currentDetails, telemetry)
                        except Exception:  # noqa: BLE001 - ensure telemetry thread continues running
                            logging.exception(
                                "Failed to send automatic Online status for printer %s", ipAddress
                            )
                updates.append({"index": index, "changes": pendingChanges})
        if updates:
            self.printerStatusQueue.put(("updates", updates))
        self.printerStatusQueue.put(("complete", None))

    def _collectPrinterTelemetry(self, printer: Dict[str, Any]) -> Dict[str, Any]:
        ipAddress = str(printer.get("ipAddress", "")).strip()
        availabilityStatus = self._probePrinterAvailability(ipAddress) if ipAddress else "Offline"
        telemetry: Dict[str, Any] = {
            "status": availabilityStatus,
            "nozzleTemp": None,
            "bedTemp": None,
            "progressPercent": None,
            "remainingTimeSeconds": None,
            "gcodeState": None,
        }
        if availabilityStatus == "Offline":
            return telemetry

        serialNumber = self._parseOptionalString(printer.get("serialNumber"))
        accessCode = self._parseOptionalString(printer.get("accessCode"))
        brand = self._parseOptionalString(printer.get("brand"))

        looksLikeBambu = brand is None or "bambu" in brand.lower()
        if serialNumber and accessCode and looksLikeBambu:
            try:
                bambuTelemetry = self._fetchBambuTelemetry(ipAddress, serialNumber, accessCode)
                if bambuTelemetry:
                    telemetry.update(bambuTelemetry)
            except Exception as error:  # noqa: BLE001 - telemetry is best-effort
                logging.debug("Unable to fetch Bambu telemetry from %s: %s", ipAddress, error)

        return telemetry

    def _sendAutomaticPrinterStatus(
        self,
        printerIndex: int,
        currentDetails: Dict[str, Any],
        telemetry: Dict[str, Any],
    ) -> None:
        manualDefaults = currentDetails.get("manualStatusDefaults")
        if not isinstance(manualDefaults, dict) or not manualDefaults:
            logging.debug(
                "Skipping automatic status update for %s because manual defaults are missing.",
                currentDetails.get("nickname") or currentDetails.get("ipAddress") or printerIndex,
            )
            return

        statusUrl = getPrinterStatusEndpointUrl()
        if not statusUrl:
            logging.warning(
                "Skipping automatic status update for %s because the status endpoint is undefined.",
                currentDetails.get("nickname") or currentDetails.get("ipAddress") or printerIndex,
            )
            return

        apiKeyCandidate = (
            getattr(self, "listenerStatusApiKey", "") or os.getenv("BASE44_API_KEY", "").strip()
        )
        if not apiKeyCandidate:
            logging.warning(
                "Skipping automatic status update for %s because the API key is missing.",
                currentDetails.get("nickname") or currentDetails.get("ipAddress") or printerIndex,
            )
            return

        printerIpAddress = self._parseOptionalString(currentDetails.get("ipAddress")) or ""
        if not printerIpAddress:
            logging.warning(
                "Unable to send automatic status update for printer index %s due to missing IP address.",
                printerIndex,
            )
            return

        payload = dict(manualDefaults)
        payload["printerIpAddress"] = printerIpAddress
        payload["status"] = "Online"

        jobProgressCandidate: Any = telemetry.get("progressPercent")
        if jobProgressCandidate is None:
            jobProgressCandidate = manualDefaults.get("jobProgress")
        jobProgressValue = self._parseOptionalFloat(jobProgressCandidate)
        if jobProgressValue is not None and jobProgressValue >= 0:
            payload["jobProgress"] = float(jobProgressValue)

        nozzleTempValue = self._parseOptionalFloat(telemetry.get("nozzleTemp"))
        if nozzleTempValue is not None:
            payload["nozzleTemp"] = nozzleTempValue
        elif payload.get("nozzleTemp") is None:
            payload.pop("nozzleTemp", None)

        bedTempValue = self._parseOptionalFloat(telemetry.get("bedTemp"))
        if bedTempValue is not None:
            payload["bedTemp"] = bedTempValue
        elif payload.get("bedTemp") is None:
            payload.pop("bedTemp", None)

        remainingSecondsValue = self._parseOptionalInt(telemetry.get("remainingTimeSeconds"))
        if remainingSecondsValue is not None and remainingSecondsValue >= 0:
            payload["remainingTimeSeconds"] = remainingSecondsValue

        gcodeStateValue = self._parseOptionalString(telemetry.get("gcodeState"))
        if gcodeStateValue:
            payload["gcodeState"] = gcodeStateValue

        if not isinstance(payload.get("materialLevel"), dict):
            payload["materialLevel"] = {}

        payload["lastUpdateTimestamp"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"

        recipientCandidate = (
            getattr(self, "listenerRecipientId", "")
            or os.getenv("BASE44_RECIPIENT_ID", "").strip()
        )
        if not recipientCandidate:
            logging.warning(
                "Skipping automatic status update for %s because the recipient ID is missing.",
                currentDetails.get("nickname") or printerIpAddress or printerIndex,
            )
            return

        payload["recipientId"] = recipientCandidate

        payload = addPrinterIdentityToPayload(
            payload,
            self._parseOptionalString(currentDetails.get("serialNumber")),
            self._parseOptionalString(currentDetails.get("accessCode")),
        )

        headers = {"X-API-Key": apiKeyCandidate, "Content-Type": "application/json"}
        try:
            response = requests.post(statusUrl, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
        except requests.RequestException as error:
            logging.warning(
                "Automatic Online status update failed for %s: %s",
                currentDetails.get("nickname") or printerIpAddress,
                error,
            )
            return

        logging.info(
            "Automatic Online status update sent for %s: %s",
            currentDetails.get("nickname") or printerIpAddress,
            response.status_code,
        )

        manualDefaultsUpdate = dict(manualDefaults)
        manualDefaultsUpdate["status"] = "Online"
        manualDefaultsUpdate["lastUpdateTimestamp"] = payload["lastUpdateTimestamp"]
        if jobProgressValue is not None and jobProgressValue >= 0:
            manualDefaultsUpdate["jobProgress"] = float(jobProgressValue)
        if nozzleTempValue is not None:
            manualDefaultsUpdate["nozzleTemp"] = nozzleTempValue
        if bedTempValue is not None:
            manualDefaultsUpdate["bedTemp"] = bedTempValue

        sanitizedDefaults = self._sanitizeManualStatusDefaults(manualDefaultsUpdate)

        updates: Dict[str, Any] = {
            "manualStatusDefaults": sanitizedDefaults,
        }

        self.printerStatusQueue.put(("updates", [{"index": printerIndex, "changes": updates}]))

        printerName = currentDetails.get("nickname") or printerIpAddress
        logMessage = f"Sent automatic Online status for {printerName}."
        self.logQueue.put(logMessage)

    def _fetchBambuTelemetry(
        self,
        ipAddress: str,
        serialNumber: str,
        accessCode: str,
        timeoutSeconds: float = 4.0,
    ) -> Dict[str, Any]:
        try:
            import bambulabs_api as bl  # type: ignore[import]
        except Exception:
            return {}

        try:
            printer = bl.Printer(ipAddress, accessCode, serialNumber)
        except Exception:
            return {}

        try:
            mqttStart = getattr(printer, "mqtt_start", None)
            if callable(mqttStart):
                mqttStart()
        except Exception:
            with contextlib.suppress(Exception):
                printer.disconnect()
            return {}

        connectMethod = getattr(printer, "connect", None)
        if callable(connectMethod):
            try:
                connectMethod()
            except Exception:
                pass

        try:
            readinessDeadline = time.monotonic() + max(timeoutSeconds, 0.0)
            statePayload: Any = None
            while time.monotonic() < readinessDeadline:
                try:
                    statePayload = printer.get_state()
                    break
                except Exception:
                    time.sleep(0.2)

            try:
                percentagePayload: Any = printer.get_percentage()
            except Exception:
                percentagePayload = None

            gcodePayload: Any = None
            gcodeGetter = getattr(printer, "get_gcode_state", None)
            if callable(gcodeGetter):
                try:
                    gcodePayload = gcodeGetter()
                except Exception:
                    gcodePayload = None

            def searchValue(payload: Any, keys: set[str]) -> Any:
                if payload is None:
                    return None
                if isinstance(payload, dict):
                    for key, value in payload.items():
                        if key in keys:
                            return value
                        nested = searchValue(value, keys)
                        if nested is not None:
                            return nested
                elif isinstance(payload, (list, tuple)):
                    for item in payload:
                        nested = searchValue(item, keys)
                        if nested is not None:
                            return nested
                return None

            def pickPercentage() -> Any:
                if isinstance(percentagePayload, (int, float, str, bool)):
                    return percentagePayload
                return searchValue(statePayload, {"mc_percent", "print_percent", "percent"})

            def pickRemaining() -> Any:
                return searchValue(statePayload, {"mc_remaining_time", "remaining_time", "time_remaining"})

            def pickState() -> Any:
                candidate = searchValue(gcodePayload, {"gcode_state", "sub_state", "state"})
                if candidate is None:
                    candidate = searchValue(statePayload, {"gcode_state", "sub_state", "state", "printer_state"})
                return candidate

            def pickNozzle() -> Any:
                return searchValue(statePayload, {"nozzle_temper", "nozzle_temp", "nozzle_target_temper"})

            def pickBed() -> Any:
                return searchValue(statePayload, {"bed_temper", "bed_temp", "bed_target_temper"})

            statusPayload = {
                "mc_percent": pickPercentage(),
                "mc_remaining_time": pickRemaining(),
                "gcode_state": pickState(),
                "nozzle_temper": pickNozzle(),
                "bed_temper": pickBed(),
            }

            return self._interpretBambuStatus(statusPayload)
        finally:
            with contextlib.suppress(Exception):
                printer.disconnect()

    def _interpretBambuStatus(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        percent = self._parseOptionalFloat(payload.get("mc_percent"))
        remainingSeconds = self._parseOptionalInt(payload.get("mc_remaining_time"))
        nozzleTemp = self._parseOptionalFloat(payload.get("nozzle_temper"))
        bedTemp = self._parseOptionalFloat(payload.get("bed_temper"))
        state = self._parseOptionalString(payload.get("gcode_state"))

        status = self._mapBambuState(state, percent)

        return {
            "status": status,
            "progressPercent": percent,
            "remainingTimeSeconds": remainingSeconds,
            "nozzleTemp": nozzleTemp,
            "bedTemp": bedTemp,
            "gcodeState": state,
        }

    def _mapBambuState(self, state: Optional[str], percent: Optional[float]) -> str:
        normalized = state.strip().upper() if state else ""
        mapping = {
            "IDLE": "Idle",
            "READY": "Idle",
            "STANDBY": "Idle",
            "PRINTING": "Printing",
            "RUNNING": "Printing",
            "PAUSE": "Paused",
            "PAUSED": "Paused",
            "FINISH": "Completed",
            "FINISHED": "Completed",
            "COMPLETED": "Completed",
            "FAILED": "Error",
            "FAIL": "Error",
            "ERROR": "Error",
            "OFFLINE": "Offline",
        }
        if normalized in mapping:
            return mapping[normalized]
        if normalized:
            return normalized.title()
        if percent is not None and percent > 0:
            return "Printing"
        return "Online"

    def _probePrinterAvailability(self, ipAddress: str, timeoutSeconds: float = 2.0) -> str:
        if not ipAddress:
            return "Offline"
        portsToTry = (8883, 443, 80)
        for port in portsToTry:
            try:
                with contextlib.closing(socket.create_connection((ipAddress, port), timeoutSeconds)):
                    return "Online"
            except (OSError, ValueError):
                continue
        return "Offline"

    def _processPrinterStatusUpdates(self) -> None:
        try:
            while True:
                messageType, payload = self.printerStatusQueue.get_nowait()
                if messageType == "updates":
                    updatesPayload = payload if isinstance(payload, list) else []
                    hasChanges = False
                    for item in updatesPayload:
                        if isinstance(item, dict):
                            index = item.get("index")
                            changes = item.get("changes")
                            if (
                                isinstance(index, int)
                                and isinstance(changes, dict)
                                and 0 <= index < len(self.printers)
                            ):
                                self.printers[index].update(changes)
                                hasChanges = True
                        elif isinstance(item, (tuple, list)) and len(item) == 2:
                            indexCandidate, statusCandidate = item
                            if (
                                isinstance(indexCandidate, int)
                                and 0 <= indexCandidate < len(self.printers)
                            ):
                                self.printers[indexCandidate]["status"] = str(statusCandidate)
                                hasChanges = True
                    if hasChanges:
                        self._savePrinters()
                        self._refreshPrinterList()
                elif messageType == "complete":
                    self.statusRefreshThread = None
                    if hasattr(self, "connectPrintersButton"):
                        self.connectPrintersButton.config(state=tk.NORMAL)
                    delay = 0 if self.pendingImmediateStatusRefresh else self.statusRefreshIntervalMs
                    self._scheduleStatusRefresh(delay)
        except Empty:
            pass
        self.root.after(500, self._processPrinterStatusUpdates)

    def _collectActiveLanPrinters(self) -> list[Dict[str, Any]]:
        active: list[Dict[str, Any]] = []
        for printer in self.printers:
            if not isinstance(printer, dict):
                continue
            connection = str(
                printer.get("transport") or printer.get("connectionMethod") or ""
            ).strip().lower()
            if connection != "lan":
                continue
            ipAddress = str(printer.get("ipAddress") or "").strip()
            serialNumber = str(printer.get("serialNumber") or "").strip()
            accessCode = str(printer.get("accessCode") or "").strip()
            if not ipAddress or not serialNumber or not accessCode:
                continue
            active.append(
                {
                    "ipAddress": ipAddress,
                    "serialNumber": serialNumber,
                    "accessCode": accessCode,
                    "nickname": printer.get("nickname"),
                    "brand": printer.get("brand"),
                }
            )
        return active

    def _startStatusSubscribers(self) -> None:
        if not self.liveStatusEnabledVar.get():
            return
        if not self.statusSubscriber:
            return
        self._applyBase44Environment()
        activeLanPrinters = self._collectActiveLanPrinters()
        if activeLanPrinters:
            self.statusSubscriber.startAll(activeLanPrinters)
        self._startCommandWorkers()

    def _stopStatusSubscribers(self) -> None:
        if self.statusSubscriber:
            self.statusSubscriber.stopAll()
        self.lastLiveStatusAlerts.clear()
        self._stopCommandWorkers()

    def _startCommandWorkers(self) -> None:
        if not self.liveStatusEnabledVar.get():
            return
        if not self.listenerThread or not self.listenerThread.is_alive():
            return
        self._applyBase44Environment()
        controlBaseUrlCandidate = self.baseUrlVar.get().strip() or defaultBaseUrl
        try:
            controlBaseUrl = buildBaseUrl(controlBaseUrlCandidate)
        except ValueError:
            controlBaseUrl = buildBaseUrl(defaultBaseUrl)
        # Use the value as configured in the UI without reducing it internally.
        pollIntervalSeconds = max(3.0, float(max(1, int(self.pollIntervalVar.get()))))
        activeLanPrinters = self._collectActiveLanPrinters()
        activeSerials: set[str] = set()
        for printer in activeLanPrinters:
            serialNumber = str(printer.get("serialNumber") or "").strip()
            ipAddress = str(printer.get("ipAddress") or "").strip()
            accessCode = str(printer.get("accessCode") or "").strip()
            if not (serialNumber and ipAddress and accessCode):
                continue
            activeSerials.add(serialNumber)
            if serialNumber in self.commandWorkers:
                continue
            try:
                worker = CommandWorker(
                    serial=serialNumber,
                    ipAddress=ipAddress,
                    accessCode=accessCode,
                    nickname=printer.get("nickname"),
                    apiKey=self.listenerControlApiKey or None,
                    recipientId=self.listenerRecipientId,
                    baseUrl=controlBaseUrl,
                    pollInterval=pollIntervalSeconds,
                )
                worker.start()
                self.commandWorkers[serialNumber] = worker
                self.log(f"Command worker started for {serialNumber} ({ipAddress})")
            except Exception as error:
                self.log(f"Failed to start command worker for {serialNumber}: {error}")
        for serialNumber, worker in list(self.commandWorkers.items()):
            if serialNumber in activeSerials:
                continue
            try:
                worker.stop()
                self.log(f"Command worker stopped for {serialNumber}")
            except Exception as error:
                self.log(f"Failed to stop command worker for {serialNumber}: {error}")
            finally:
                self.commandWorkers.pop(serialNumber, None)

    def _stopCommandWorkers(self) -> None:
        for serialNumber, worker in list(self.commandWorkers.items()):
            try:
                worker.stop()
                self.log(f"Command worker stopped for {serialNumber}")
            except Exception as error:
                self.log(f"Failed to stop command worker for {serialNumber}: {error}")
        self.commandWorkers.clear()

    def _handleLiveStatusToggle(self) -> None:
        if not self.liveStatusEnabledVar.get():
            self._stopStatusSubscribers()
        else:
            if self.listenerThread and self.listenerThread.is_alive():
                self._startStatusSubscribers()

    def _onPrinterStatusUpdate(self, status: Dict[str, Any], printerConfig: Dict[str, Any]) -> None:
        statusCopy = dict(status)
        printerConfigCopy = dict(printerConfig)
        try:
            postStatus(statusCopy, printerConfigCopy)
        except Exception:
            logging.debug("Failed to post status from subscriber", exc_info=True)

        try:
            self.root.after(0, lambda: self._applyLiveStatusUpdate(statusCopy, printerConfigCopy))
        except Exception:
            logging.exception("Unable to schedule GUI update for printer status")

    def _onPrinterStatusError(self, message: str, printerConfig: Dict[str, Any]) -> None:
        printerCopy = dict(printerConfig)
        accessCode = str(printerCopy.get("accessCode") or "")
        safeMessage = message.replace(accessCode, "***") if accessCode else message
        serial = str(printerCopy.get("serialNumber") or "")
        ipAddress = str(printerCopy.get("ipAddress") or "")
        identifier = serial or ipAddress or "unknown"
        logEntry = f"Status error for {identifier}: {safeMessage}"
        self.logQueue.put(logEntry)

        def showWarning() -> None:
            try:
                messagebox.showwarning("Printer Status", logEntry, parent=self.root)
            except Exception:
                logging.exception("Failed to show status warning dialog")

        try:
            self.root.after(0, showWarning)
        except Exception:
            logging.exception("Unable to schedule status warning dialog")

    def _applyLiveStatusUpdate(self, status: Dict[str, Any], printerConfig: Dict[str, Any]) -> None:
        serial = str(printerConfig.get("serialNumber") or status.get("printerSerial") or "").strip()
        if not serial:
            return

        mappedStatus = self._mapBambuState(
            self._parseOptionalString(status.get("gcodeState") or status.get("state")),
            status.get("progressPercent") if isinstance(status.get("progressPercent"), (int, float)) else None,
        )

        updated = False
        for printer in self.printers:
            if not isinstance(printer, dict):
                continue
            if str(printer.get("serialNumber") or "").strip() != serial:
                continue
            printer["status"] = mappedStatus
            if "gcodeState" in status:
                printer["gcodeState"] = status.get("gcodeState")
            if status.get("progressPercent") is not None:
                printer["progressPercent"] = status.get("progressPercent")
            if status.get("remainingTimeSeconds") is not None:
                printer["remainingTimeSeconds"] = status.get("remainingTimeSeconds")
            if status.get("nozzleTemp") is not None:
                printer["nozzleTemp"] = status.get("nozzleTemp")
            if status.get("bedTemp") is not None:
                printer["bedTemp"] = status.get("bedTemp")
            updated = True
            break

        if updated:
            self._refreshPrinterList()

        alertCode = str(status.get("hmsCode") or "").strip()
        alertMessage = str(status.get("errorMessage") or "").strip()
        if alertCode or alertMessage:
            alertKey = f"{alertCode}|{alertMessage}"
            previousAlert = self.lastLiveStatusAlerts.get(serial)
            if previousAlert != alertKey:
                self.lastLiveStatusAlerts[serial] = alertKey
                self._showPrinterAlert(serial, printerConfig, alertCode, alertMessage)
        else:
            self.lastLiveStatusAlerts.pop(serial, None)

    def _showPrinterAlert(
        self,
        serial: str,
        printerConfig: Dict[str, Any],
        hmsCode: Optional[str],
        errorMessage: Optional[str],
    ) -> None:
        printerName = printerConfig.get("nickname") or serial
        details: list[str] = []
        if hmsCode:
            details.append(f"HMS code: {hmsCode}")
        if errorMessage:
            details.append(errorMessage)
        if not details:
            details.append("Printer reported an issue.")
        messageText = "\n".join(details)
        logEntry = f"Alert for {printerName}: {messageText}"
        self.logQueue.put(logEntry)

        try:
            messagebox.showwarning(f"Printer Alert - {printerName}", messageText, parent=self.root)
        except Exception:
            logging.exception("Failed to display printer alert dialog")

    def _chooseOutputDir(self) -> None:
        selectedDir = filedialog.askdirectory(title="Select Output Directory")
        if selectedDir:
            self.outputDirVar.set(selectedDir)

    def _chooseLogFile(self) -> None:
        selectedFile = filedialog.asksaveasfilename(
            title="Select JSON Log File",
            defaultextension=".json",
            filetypes=(("JSON Files", "*.json"), ("All Files", "*.*")),
        )
        if selectedFile:
            self.logFileVar.set(selectedFile)

    def startListening(self) -> None:
        if self.listenerThread and self.listenerThread.is_alive():
            messagebox.showinfo("Listener", "Listener is already running.")
            return

        baseUrl = self.baseUrlVar.get().strip()
        recipientId = self.recipientVar.get().strip()
        outputDir = self.outputDirVar.get().strip()
        logFile = self.logFileVar.get().strip()
        pollInterval = max(5, int(self.pollIntervalVar.get()))

        if not baseUrl or not recipientId:
            messagebox.showerror("Missing Information", "Base URL and recipient ID are required.")
            return

        # Bruk samme Recipient ID og API-key for status- og command-arbeidere.
        self.listenerRecipientId = recipientId
        self.listenerStatusApiKey = (
            self.statusApiKeyVar.get().strip() if hasattr(self, "statusApiKeyVar") else ""
        )
        # Gjør API- og mottakerverdier tilgjengelig for alle bakgrunnstråder.
        self._applyBase44Environment()

        try:
            ensureOutputDirectory(outputDir)
        except OSError as error:
            messagebox.showerror("Output Directory", f"Unable to prepare output directory: {error}")
            return

        self.logFilePath = Path(logFile).expanduser().resolve()
        self.stopEvent = threading.Event()
        self.listenerThread = threading.Thread(
            target=self._runListener,
            args=(baseUrl, recipientId, outputDir, pollInterval),
            daemon=True,
        )
        self.listenerThread.start()
        self._appendLogLine("Started listening.")
        self.startButton.config(state=tk.DISABLED)
        self.stopButton.config(state=tk.NORMAL)
        if self.liveStatusEnabledVar.get():
            try:
                self._startStatusSubscribers()
            except Exception:  # pragma: no cover - defensive logging
                logging.exception("Failed to start live status subscribers")

    def stopListening(self) -> None:
        self._stopStatusSubscribers()
        if self.stopEvent:
            self.stopEvent.set()
        if self.listenerThread and self.listenerThread.is_alive() and threading.current_thread() != self.listenerThread:
            self.listenerThread.join(timeout=0.5)
        self.listenerThread = None
        self.stopEvent = None
        self.startButton.config(state=tk.NORMAL)
        self.stopButton.config(state=tk.DISABLED)
        self._appendLogLine("Stopped listening.")

    def _runListener(
        self,
        baseUrl: str,
        recipientId: str,
        outputDir: str,
        pollInterval: int,
    ) -> None:
        try:
            listenForFiles(
                baseUrl,
                recipientId,
                outputDir,
                pollInterval,
                maxIterations=0,
                onFileFetched=self._handleFetchedData,
                stopEvent=self.stopEvent,
                logFilePath=str(self.logFilePath) if self.logFilePath else None,
            )
        except Exception as error:  # noqa: BLE001 - surface exceptions to the GUI
            logging.exception("Listener encountered an error: %s", error)
            self.logQueue.put(f"Error: {error}")
        finally:
            self.logQueue.put("__LISTENER_STOPPED__")

    def onFileDownloaded(self, path: Path, metadata: Dict[str, Any]) -> None:
        printerConfig = pickPrinter(metadata)
        if not printerConfig:
            self.log("Ingen printer i printers.json – kan ikke sende.")
            return

        def resolveText(key: str) -> Optional[str]:
            for source in (metadata, printerConfig):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        def resolveBool(key: str, default: bool) -> bool:
            for source in (metadata, printerConfig):
                if key in source:
                    interpreted = interpretBoolean(source[key])
                    if interpreted is not None:
                        return interpreted
            return default

        def resolveUseAms(default: Optional[bool] = None) -> Optional[bool]:
            for source in (metadata, printerConfig):
                if "useAms" in source:
                    value = source["useAms"]
                    if isinstance(value, bool):
                        return value
                    if isinstance(value, str):
                        normalized = value.strip().lower()
                        if normalized in {"auto", "", "none", "null"}:
                            return None
                    interpreted = interpretBoolean(value)
                    if interpreted is not None:
                        return interpreted
            return default

        def resolveInt(key: str, default: Optional[int]) -> Optional[int]:
            for source in (metadata, printerConfig):
                if key in source:
                    interpreted = interpretInteger(source[key])
                    if interpreted is not None:
                        return interpreted
            return default


        # Determine transport and optional cloudUrl
        selectedTransport = extractPreferredTransport(metadata, printerConfig) or (
            "bambu_connect" if interpretBoolean(printerConfig.get("useCloud")) else "lan"
        )
        cloudUrlValue = (metadata.get("cloudUrl") or printerConfig.get("cloudUrl"))

        def normalizeString(value: object) -> str:
            stringValue = str(value).strip() if value is not None else ""
            return "" if stringValue.lower() in {"", "none", "null"} else stringValue

        metadataIp = normalizeString(
            metadata.get("printer_ip") or metadata.get("ipAddress")
        )
        metadataSerial = normalizeString(
            metadata.get("printer_serial") or metadata.get("serialNumber")
        )
        metadataAccessCode = normalizeString(
            metadata.get("printer_access_code") or metadata.get("accessCode")
        )

        configuredIp = normalizeString(printerConfig.get("ipAddress"))
        configuredSerial = normalizeString(printerConfig.get("serialNumber"))
        configuredAccessCode = normalizeString(printerConfig.get("accessCode"))

        if metadataSerial:
            if not configuredSerial:
                self.log(
                    "Printer i printers.json mangler serienummer – kan ikke bekrefte metadata, avbryter."
                )
                return
            if metadataSerial != configuredSerial:
                self.log(
                    "Printer mismatch (metadata serial="
                    f"{metadataSerial}, valgt serial={configuredSerial}) – avbryter."
                )
                return

        ipAddressValue = metadataIp or configuredIp
        serialValue = metadataSerial or configuredSerial
        accessCodeValue = metadataAccessCode or configuredAccessCode

        def isValidIp(ipValue: str) -> bool:
            try:
                ip_address(ipValue)
                return True
            except ValueError:
                return False

        if selectedTransport != "bambu_connect":
            self.log(
                "LAN creds: ip="
                f"{ipAddressValue!r}, serial={serialValue!r}, access={'OK' if accessCodeValue else 'MISSING'}"
            )
            if not ipAddressValue or not accessCodeValue or not serialValue:
                self.log(
                    "Mangler LAN-informasjon: ip="
                    f"{ipAddressValue!r}, serial={serialValue!r}, access={'OK' if accessCodeValue else 'MISSING'} – hopper over sending."
                )
                return
            if metadataIp:
                if not isValidIp(metadataIp):
                    self.log(f"Ugyldig IP i metadata: {metadataIp!r} – avbryter.")
                    return
                if configuredIp and configuredIp != metadataIp:
                    self.log(
                        f"IP mismatch (metadata {metadataIp} != valgt {configuredIp}) – avbryter."
                    )
                    return

        lanStrategyValue = resolveText("lanStrategy") or str(printerConfig.get("lanStrategy") or "legacy")
        plateIndexValue = resolveInt("plateIndex", None)
        if plateIndexValue is None:
            plateIndexValue = 1
        waitSecondsValue = resolveInt("waitSeconds", None)
        if waitSecondsValue is None:
            waitSecondsValue = 8

        options = BambuPrintOptions(
            ipAddress=ipAddressValue,
            serialNumber=serialValue,
            accessCode=accessCodeValue,
            useAms=resolveUseAms(),
            bedLeveling=resolveBool("bedLeveling", True),
            layerInspect=resolveBool("layerInspect", True),
            flowCalibration=resolveBool("flowCalibration", False),
            vibrationCalibration=resolveBool("vibrationCalibration", False),
            secureConnection=resolveBool("secureConnection", False),
            lanStrategy=lanStrategyValue,
            plateIndex=plateIndexValue,
            waitSeconds=waitSecondsValue,
            useCloud=(selectedTransport == "bambu_connect"),
            cloudUrl=cloudUrlValue,
            transport=selectedTransport,
        )

        def worker() -> None:
            try:
                self.log(f"Sender til Bambu: {path}")
                sendBambuPrintJob(
                    filePath=path,
                    options=options,
                    statusCallback=lambda status: (
                        self.log(json.dumps(status)),
                        postStatus(status, printerConfig),
                    ),
                )
                self.log("Startkommando sendt.")
            except Exception as error:  # noqa: BLE001 - surface errors to log
                self.log(f"Feil ved sending: {error}")

        threading.Thread(target=worker, daemon=True).start()

    def _handleFetchedData(self, details: Dict[str, object]) -> None:
        savedFile = details.get("savedFile") or details.get("fileName") or "metadata"
        logMessage = f"Fetched file: {savedFile}"

        statusDetails = details.get("productStatus")
        if isinstance(statusDetails, dict):
            availability = statusDetails.get("availabilityStatus")
            downloaded = statusDetails.get("downloaded")
            logMessage += f" | Status: {availability} (downloaded={downloaded})"

        if isinstance(details.get("logFilePath"), str):
            logMessage += f" | Metadata saved to {details['logFilePath']}"
        elif self.logFilePath is not None:
            try:
                loggedPath = appendJsonLogEntry(self.logFilePath, details)
                logMessage += f" | Metadata saved to {loggedPath}"
            except Exception as error:  # noqa: BLE001 - ensure UI feedback on errors
                logging.exception("Failed to append JSON log: %s", error)
                logMessage += f" | Failed to write log: {error}"
        self.logQueue.put(logMessage)

        savedPathValue = details.get("savedFile")
        if isinstance(savedPathValue, (str, Path)):
            combinedMetadata: Dict[str, Any] = {}
            for key in ("metadata", "unencryptedData", "decryptedData"):
                source = details.get(key)
                if isinstance(source, dict):
                    combinedMetadata.update(source)
            for key in ("serialNumber", "nickname", "ipAddress", "accessCode"):
                if key not in combinedMetadata and key in details:
                    combinedMetadata[key] = details[key]
            self.onFileDownloaded(Path(savedPathValue), combinedMetadata)

    def _appendLogLine(self, message: str) -> None:
        self.logText.configure(state=tk.NORMAL)
        self.logText.insert(tk.END, f"{message}\n")
        self.logText.see(tk.END)
        self.logText.configure(state=tk.DISABLED)

    def _processLogQueue(self) -> None:
        try:
            while True:
                message = self.logQueue.get_nowait()
                if message == "__LISTENER_STOPPED__":
                    self.listenerThread = None
                    self.stopEvent = None
                    self.startButton.config(state=tk.NORMAL)
                    self.stopButton.config(state=tk.DISABLED)
                    self._appendLogLine("Listener stopped.")
                else:
                    self._appendLogLine(message)
        except Empty:
            pass
        self.root.after(200, self._processLogQueue)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.stopListening()

    def _handleWindowClose(self) -> None:
        try:
            self.stopListening()
        finally:
            try:
                self.root.destroy()
            except Exception:
                logging.exception("Failed to destroy root window")


def runGui() -> None:
    app = ListenerGuiApp()
    app.run()


if __name__ == "__main__":
    runGui()
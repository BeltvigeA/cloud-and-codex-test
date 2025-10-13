"""Simple GUI application for listening to channels and logging data to JSON."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import ssl
import threading
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .base44_status import Base44StatusReporter, getDefaultPrinterApiToken

from .bambuPrinter import (
    BambuPrintOptions,
    postStatus,
    sendBambuPrintJob,
    startStatusHeartbeat,
    upsertPrinterFromJobMetadata,
    waitForMqttReady,
)
from .client import (
    appendJsonLogEntry,
    configureLogging,
    defaultBaseUrl,
    defaultFilesDirectory,
    ensureOutputDirectory,
    interpretBoolean,
    interpretInteger,
    listenForFiles,
)

try:  # pragma: no cover - optional dependency in some test environments
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError:  # pragma: no cover - gracefully handled when MQTT is unavailable
    mqtt = None  # type: ignore


try:  # pragma: no cover - optional dependency in GUI environments
    import bambulabs_api as bambuApi  # type: ignore
except ImportError:  # pragma: no cover - handled when Developer Mode packages missing
    bambuApi = None  # type: ignore


def addPrinterIdentityToPayload(
    payload: Dict[str, Any], printerSerial: Optional[str], accessCode: Optional[str]
) -> Dict[str, Any]:
    if printerSerial:
        payload["printerSerial"] = printerSerial
    if accessCode:
        payload["accessCode"] = accessCode
    return payload


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

        self.logQueue: "Queue[str]" = Queue()
        self.listenerThread: Optional[threading.Thread] = None
        self.stopEvent: Optional[threading.Event] = None
        self.logFilePath: Optional[Path] = None

        self.printerStoragePath = Path.home() / ".printmaster" / "printers.json"
        self.printers: list[Dict[str, Any]] = self._loadPrinters()
        self.printerStatusQueue: "Queue[tuple[str, Any]]" = Queue()
        self.statusRefreshThread: Optional[threading.Thread] = None
        self.statusRefreshIntervalMs = 30_000
        self.pendingImmediateStatusRefresh = False

        self.statusHeartbeatEvents: Dict[str, threading.Event] = {}

        self.listenerRecipientId = ""
        self.listenerStatusApiKey = ""

        self.base44Reporter = Base44StatusReporter(
            getRecipientId=self._getActiveRecipientIdForBase44,
            getApiKey=self._getBase44ApiKey,
            listConnectedPrinters=self._listPrintersForBase44,
            buildSnapshot=self._buildBase44Snapshot,
            intervalSeconds=5.0,
        )

        self._buildLayout()
        self.root.after(200, self._processLogQueue)
        self.root.after(200, self._processPrinterStatusUpdates)
        self._scheduleStatusRefresh(0)

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
        ttk.Label(parent, text="Base URL:").grid(row=0, column=0, sticky=tk.W, **paddingOptions)
        self.baseUrlVar = tk.StringVar(value=defaultBaseUrl)
        ttk.Entry(parent, textvariable=self.baseUrlVar, width=50).grid(
            row=0, column=1, sticky=tk.EW, **paddingOptions
        )

        ttk.Label(parent, text="Channel (Recipient ID):").grid(
            row=1, column=0, sticky=tk.W, **paddingOptions
        )
        self.recipientVar = tk.StringVar()
        self.recipientVar.trace_add("write", lambda *_: self._updateListenerRecipient())
        ttk.Entry(parent, textvariable=self.recipientVar, width=30).grid(
            row=1, column=1, sticky=tk.EW, **paddingOptions
        )
        self._updateListenerRecipient()

        ttk.Label(parent, text="Status API Key:").grid(row=2, column=0, sticky=tk.W, **paddingOptions)
        self.statusApiKeyVar = tk.StringVar()
        self.statusApiKeyVar.trace_add("write", lambda *_: self._updateListenerStatusApiKey())
        ttk.Entry(parent, textvariable=self.statusApiKeyVar, width=30, show="*").grid(
            row=2, column=1, sticky=tk.EW, **paddingOptions
        )
        self._updateListenerStatusApiKey()

        ttk.Label(parent, text="Output Directory:").grid(row=3, column=0, sticky=tk.W, **paddingOptions)
        self.outputDirVar = tk.StringVar(value=str(defaultFilesDirectory))
        outputDirFrame = ttk.Frame(parent)
        outputDirFrame.grid(row=3, column=1, sticky=tk.EW, **paddingOptions)
        outputDirEntry = ttk.Entry(outputDirFrame, textvariable=self.outputDirVar, width=40)
        outputDirEntry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(outputDirFrame, text="Browse", command=self._chooseOutputDir).pack(side=tk.LEFT, padx=4)

        ttk.Label(parent, text="JSON Log File:").grid(row=4, column=0, sticky=tk.W, **paddingOptions)
        self.logFileVar = tk.StringVar(
            value=str(Path.home() / ".printmaster" / "listener-log.json")
        )
        logFileFrame = ttk.Frame(parent)
        logFileFrame.grid(row=4, column=1, sticky=tk.EW, **paddingOptions)
        logFileEntry = ttk.Entry(logFileFrame, textvariable=self.logFileVar, width=40)
        logFileEntry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(logFileFrame, text="Browse", command=self._chooseLogFile).pack(side=tk.LEFT, padx=4)

        ttk.Label(parent, text="Poll Interval (seconds):").grid(row=5, column=0, sticky=tk.W, **paddingOptions)
        self.pollIntervalVar = tk.IntVar(value=30)
        ttk.Spinbox(parent, from_=5, to=3600, textvariable=self.pollIntervalVar).grid(
            row=5, column=1, sticky=tk.W, **paddingOptions
        )

        buttonFrame = ttk.Frame(parent)
        buttonFrame.grid(row=6, column=0, columnspan=2, pady=12)
        self.startButton = ttk.Button(buttonFrame, text="Start Listening", command=self.startListening)
        self.startButton.pack(side=tk.LEFT, padx=6)
        self.stopButton = ttk.Button(buttonFrame, text="Stop", command=self.stopListening, state=tk.DISABLED)
        self.stopButton.pack(side=tk.LEFT, padx=6)

        ttk.Label(parent, text="Event Log:").grid(row=7, column=0, sticky=tk.W, **paddingOptions)
        self.logText = tk.Text(parent, height=10, state=tk.DISABLED)
        self.logText.grid(row=7, column=1, sticky=tk.NSEW, **paddingOptions)

        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(7, weight=1)

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
        self.connectPrintersButton = ttk.Button(
            actionFrame,
            text="Connect Printers",
            command=self.refreshPrintersNow,
            state=tk.NORMAL,
        )
        self.connectPrintersButton.pack(side=tk.LEFT, padx=8)
        actionFrame.columnconfigure(0, weight=1)

        treeFrame = ttk.Frame(parent)
        treeFrame.grid(row=2, column=0, sticky=tk.NSEW, padx=8, pady=(4, 8))
        columns = (
            "nickname",
            "ipAddress",
            "accessCode",
            "serialNumber",
            "brand",
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
        self.printerTree.heading("status", text="Status")
        self.printerTree.heading("nozzleTemp", text="Nozzle Temp")
        self.printerTree.heading("bedTemp", text="Bed Temp")
        self.printerTree.heading("progress", text="Progress")
        self.printerTree.column("nickname", width=120)
        self.printerTree.column("ipAddress", width=110)
        self.printerTree.column("accessCode", width=110)
        self.printerTree.column("serialNumber", width=120)
        self.printerTree.column("brand", width=100)
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
                                    "status": str(entry.get("status", "")) or "Unknown",
                                    "nozzleTemp": self._parseOptionalFloat(entry.get("nozzleTemp")),
                                    "bedTemp": self._parseOptionalFloat(entry.get("bedTemp")),
                                    "progressPercent": self._parseOptionalFloat(entry.get("progressPercent")),
                                    "remainingTimeSeconds": self._parseOptionalInt(entry.get("remainingTimeSeconds")),
                                    "gcodeState": self._parseOptionalString(entry.get("gcodeState")),
                                    "statusBaseUrl": self._parseOptionalString(entry.get("statusBaseUrl")) or "",
                                    "statusApiKey": self._parseOptionalString(entry.get("statusApiKey")) or "",
                                    "statusRecipientId": self._parseOptionalString(entry.get("statusRecipientId")),
                                }
                            )
                        )
                    return sanitizedPrinters
            except (OSError, json.JSONDecodeError) as error:
                logging.warning("Unable to load printers from %s: %s", self.printerStoragePath, error)
        return []

    def _parseOptionalFloat(self, value: Any) -> Optional[float]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            candidate = value.strip().replace("°C", "")
            if candidate:
                try:
                    return float(candidate)
                except ValueError:
                    return None
        return None

    def _parseOptionalInt(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            candidate = (
                value.strip()
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
        printerDetails["status"] = str(printerDetails.get("status", "")) or "Unknown"
        printerDetails["nozzleTemp"] = self._parseOptionalFloat(printerDetails.get("nozzleTemp"))
        printerDetails["bedTemp"] = self._parseOptionalFloat(printerDetails.get("bedTemp"))
        printerDetails["progressPercent"] = self._parseOptionalFloat(printerDetails.get("progressPercent"))
        printerDetails["remainingTimeSeconds"] = self._parseOptionalInt(
            printerDetails.get("remainingTimeSeconds")
        )
        printerDetails["gcodeState"] = self._parseOptionalString(printerDetails.get("gcodeState"))
        printerDetails["statusBaseUrl"] = self._parseOptionalString(
            printerDetails.get("statusBaseUrl")
        ) or ""
        printerDetails["statusApiKey"] = self._parseOptionalString(
            printerDetails.get("statusApiKey")
        ) or ""
        printerDetails["statusRecipientId"] = self._parseOptionalString(
            printerDetails.get("statusRecipientId")
        )
        return printerDetails

    def _applyUpsertedPrinterRecord(self, printerRecord: Dict[str, Any]) -> None:
        serialCandidate = self._parseOptionalString(printerRecord.get("serialNumber"))
        if not serialCandidate:
            return
        loweredSerial = serialCandidate.lower()
        for index, existing in enumerate(self.printers):
            existingSerial = self._parseOptionalString(existing.get("serialNumber"))
            if existingSerial and existingSerial.lower() == loweredSerial:
                merged = dict(existing)
                merged.update(printerRecord)
                self.printers[index] = self._applyTelemetryDefaults(merged)
                self._savePrinters()
                self._refreshPrinterList()
                return
        self.printers.append(self._applyTelemetryDefaults(dict(printerRecord)))
        self._savePrinters()
        self._refreshPrinterList()

    def _formatTemperature(self, value: Optional[float]) -> str:
        if value is None:
            return "-"
        return f"{value:.1f}°C"

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
                    printer.get("status", "Unknown"),
                    nozzleTempDisplay,
                    bedTempDisplay,
                    progressDisplay,
                ),
            )
        self._onPrinterSelection(None)

    def _updateListenerRecipient(self, *_args: Any) -> None:
        self.listenerRecipientId = self.recipientVar.get().strip() if hasattr(self, "recipientVar") else ""

    def _updateListenerStatusApiKey(self, *_args: Any) -> None:
        self.listenerStatusApiKey = (
            self.statusApiKeyVar.get().strip() if hasattr(self, "statusApiKeyVar") else ""
        )

    def _getActiveRecipientIdForBase44(self) -> str:
        return self.listenerRecipientId.strip()

    def _listPrintersForBase44(self) -> list[Dict[str, Any]]:
        printersSnapshot: list[Dict[str, Any]] = []
        for printer in list(self.printers):
            if not isinstance(printer, dict):
                continue
            ipAddress = self._parseOptionalString(printer.get("ipAddress"))
            serialNumber = self._parseOptionalString(printer.get("serialNumber"))
            if not ipAddress and not serialNumber:
                continue
            printersSnapshot.append(dict(printer))
        return printersSnapshot

    def _buildBase44Snapshot(self, printer: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(printer) if isinstance(printer, dict) else {}
        ipAddress = self._parseOptionalString(record.get("ipAddress"))
        serialNumber = self._parseOptionalString(record.get("serialNumber"))
        statusValue = self._parseOptionalString(record.get("status")) or "Offline"
        normalizedStatus = statusValue.strip() or "Offline"

        explicitOnline = record.get("online")
        if isinstance(explicitOnline, bool):
            onlineFlag = explicitOnline
        else:
            mqttFlag = record.get("mqttConnected")
            onlineFlag = bool(mqttFlag) if isinstance(mqttFlag, bool) else normalizedStatus.lower() not in {
                "", "offline", "unknown"
            }

        progressCandidate = record.get("progress")
        if progressCandidate is None:
            progressCandidate = record.get("progressPercent")
        progressValue = self._parseOptionalFloat(progressCandidate)

        bedTemp = self._parseOptionalFloat(record.get("bedTemp"))
        nozzleTemp = self._parseOptionalFloat(record.get("nozzleTemp"))
        remainingCandidate = record.get("remainingTimeSeconds") or record.get("timeRemaining")
        remainingSeconds = self._parseOptionalInt(remainingCandidate)
        fanSpeed = self._parseOptionalInt(record.get("fanSpeed"))
        printSpeed = self._parseOptionalInt(record.get("printSpeed"))

        filamentUsed = record.get("filamentUsed")
        if isinstance(filamentUsed, str):
            stripped = filamentUsed.strip()
            try:
                filamentUsed = float(stripped)
            except ValueError:
                filamentUsed = stripped or None

        errorMessage = self._parseOptionalString(record.get("errorMessage")) or self._parseOptionalString(
            record.get("error")
        )
        firmwareVersion = self._parseOptionalString(record.get("firmwareVersion")) or self._parseOptionalString(
            record.get("firmware")
        )
        base44PrinterId = self._parseOptionalString(record.get("base44PrinterId")) or self._parseOptionalString(
            record.get("printerId")
        )
        currentJobId = self._parseOptionalString(record.get("currentJobId"))

        snapshot: Dict[str, Any] = {
            "ip": ipAddress,
            "serial": serialNumber,
            "status": normalizedStatus,
            "online": onlineFlag,
            "progress": progressValue,
            "currentJobId": currentJobId,
            "bedTemp": bedTemp,
            "nozzleTemp": nozzleTemp,
            "fanSpeed": fanSpeed,
            "printSpeed": printSpeed,
            "filamentUsed": filamentUsed,
            "timeRemainingSec": remainingSeconds,
            "error": errorMessage,
            "firmware": firmwareVersion,
            "base44PrinterId": base44PrinterId,
        }

        return snapshot

    def _getBase44ApiKey(self) -> str:
        if self.listenerStatusApiKey:
            return self.listenerStatusApiKey
        return getDefaultPrinterApiToken()

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
        statusBaseUrlVar = tk.StringVar(value=(initialValues or {}).get("statusBaseUrl", ""))
        statusApiKeyVar = tk.StringVar(value=(initialValues or {}).get("statusApiKey", ""))
        statusRecipientVar = tk.StringVar(value=(initialValues or {}).get("statusRecipientId", ""))
        initialStatus = (initialValues or {}).get("status", "Unknown") or "Unknown"

        ttk.Label(dialog, text="Nickname:").grid(row=0, column=0, sticky=tk.W, padx=12, pady=(12, 4))
        ttk.Entry(dialog, textvariable=nicknameVar).grid(row=0, column=1, sticky=tk.EW, padx=12, pady=(12, 4))

        ttk.Label(dialog, text="IP Address:").grid(row=1, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=ipAddressVar).grid(row=1, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Access Code:").grid(row=2, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=accessCodeVar).grid(row=2, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Serial Number:").grid(row=3, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=serialNumberVar).grid(row=3, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Brand:").grid(row=4, column=0, sticky=tk.W, padx=12, pady=4)
        brandCombo = ttk.Combobox(
            dialog,
            textvariable=brandVar,
            values=("", *self.printerBrandOptions),
        )
        brandCombo.grid(row=4, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Status Base URL:").grid(row=5, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=statusBaseUrlVar).grid(row=5, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Status API Key:").grid(row=6, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=statusApiKeyVar, show="*").grid(row=6, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Status Recipient ID:").grid(row=7, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Entry(dialog, textvariable=statusRecipientVar).grid(row=7, column=1, sticky=tk.EW, padx=12, pady=4)

        ttk.Label(dialog, text="Status:").grid(row=8, column=0, sticky=tk.W, padx=12, pady=4)
        ttk.Label(dialog, text=initialStatus).grid(row=8, column=1, sticky=tk.W, padx=12, pady=4)

        statusInfoLabel = ttk.Label(
            dialog,
            text="Status is updated automatically based on telemetry.",
        )
        statusInfoLabel.grid(row=9, column=0, columnspan=2, sticky=tk.W, padx=12, pady=(0, 4))
        statusInfoLabel.configure(foreground="gray")

        buttonFrame = ttk.Frame(dialog)
        buttonFrame.grid(row=10, column=0, columnspan=2, pady=12)
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
                statusBaseUrlVar,
                statusApiKeyVar,
                statusRecipientVar,
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
        statusBaseUrlVar: tk.StringVar,
        statusApiKeyVar: tk.StringVar,
        statusRecipientVar: tk.StringVar,
        onSave: Callable[[Dict[str, Any]], None],
    ) -> None:
        nickname = nicknameVar.get().strip()
        ipAddress = ipAddressVar.get().strip()
        accessCode = accessCodeVar.get().strip()
        serialNumber = serialNumberVar.get().strip()
        brand = brandVar.get().strip()
        statusBaseUrl = statusBaseUrlVar.get().strip()
        statusApiKey = statusApiKeyVar.get().strip()
        statusRecipientId = statusRecipientVar.get().strip()

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
            "statusBaseUrl": statusBaseUrl,
            "statusApiKey": statusApiKey,
            "statusRecipientId": statusRecipientId,
        }

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

            statusPayload = {
                "status": telemetry.get("status") or currentDetails.get("status"),
                "progress": self._parseOptionalFloat(telemetry.get("progressPercent")),
                "nozzleTemp": self._parseOptionalFloat(telemetry.get("nozzleTemp")),
                "bedTemp": self._parseOptionalFloat(telemetry.get("bedTemp")),
                "remainingTimeSeconds": self._parseOptionalInt(telemetry.get("remainingTimeSeconds")),
                "gcodeState": self._parseOptionalString(telemetry.get("gcodeState")),
                "ip": self._parseOptionalString(printer.get("ipAddress")),
                "serial": self._parseOptionalString(printer.get("serialNumber")),
                "access_code": self._parseOptionalString(printer.get("accessCode")),
                "lastSeen": datetime.now(timezone.utc).isoformat(),
            }
            try:
                postStatus(statusPayload, currentDetails)
            except Exception:  # noqa: BLE001 - heartbeat should not stop refresh loop
                logging.debug(
                    "Failed to push status heartbeat for %s", statusPayload.get("serial") or ipAddress, exc_info=True
                )

            serialCandidate = statusPayload.get("serial")
            normalizedSerial = serialCandidate.lower() if isinstance(serialCandidate, str) else None
            statusText = str(statusPayload.get("status") or "").strip().lower()
            if normalizedSerial:
                if statusText and statusText not in {"offline", "unknown"}:
                    if normalizedSerial not in self.statusHeartbeatEvents:
                        def heartbeatSupplier(serialKey: str = normalizedSerial) -> Dict[str, Any]:
                            latest = next(
                                (
                                    record
                                    for record in self.printers
                                    if self._parseOptionalString(record.get("serialNumber"))
                                    and self._parseOptionalString(record.get("serialNumber")).lower() == serialKey
                                ),
                                currentDetails,
                            )
                            return {
                                "status": latest.get("status"),
                                "progress": self._parseOptionalFloat(latest.get("progressPercent")),
                                "nozzleTemp": self._parseOptionalFloat(latest.get("nozzleTemp")),
                                "bedTemp": self._parseOptionalFloat(latest.get("bedTemp")),
                                "remainingTimeSeconds": self._parseOptionalInt(latest.get("remainingTimeSeconds")),
                                "gcodeState": self._parseOptionalString(latest.get("gcodeState")),
                                "ip": self._parseOptionalString(latest.get("ipAddress")),
                                "serial": self._parseOptionalString(latest.get("serialNumber")),
                                "access_code": self._parseOptionalString(latest.get("accessCode")),
                                "lastSeen": datetime.now(timezone.utc).isoformat(),
                            }

                        stopEvent = startStatusHeartbeat(
                            currentDetails,
                            currentDetails,
                            intervalSeconds=30.0,
                            statusSupplier=heartbeatSupplier,
                        )
                        self.statusHeartbeatEvents[normalizedSerial] = stopEvent
                elif normalizedSerial in self.statusHeartbeatEvents:
                    stopEvent = self.statusHeartbeatEvents.pop(normalizedSerial)
                    stopEvent.set()

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
                updates.append({"index": index, "changes": pendingChanges})
        if updates:
            self.printerStatusQueue.put(("updates", updates))
        self.printerStatusQueue.put(("complete", None))

    def _collectPrinterTelemetry(self, printer: Dict[str, Any]) -> Dict[str, Any]:
        ipAddress = str(printer.get("ipAddress", "")).strip()
        availabilityStatus = self._probePrinterAvailability(ipAddress) if ipAddress else "Offline"
        fallbackStatus = "Unknown" if availabilityStatus != "Offline" else "Offline"
        telemetry: Dict[str, Any] = {
            "status": fallbackStatus,
            "nozzleTemp": None,
            "bedTemp": None,
            "progressPercent": None,
            "remainingTimeSeconds": None,
            "gcodeState": None,
        }
        if not ipAddress:
            return telemetry

        serialNumber = self._parseOptionalString(printer.get("serialNumber"))
        accessCode = self._parseOptionalString(printer.get("accessCode"))
        brand = self._parseOptionalString(printer.get("brand"))
        looksLikeBambu = brand is None or "bambu" in brand.lower()

        if bambuApi is not None and serialNumber and accessCode and looksLikeBambu:
            bambuPrinter = None

            def safePrinterCall(methodName: str) -> Any:
                if bambuPrinter is None:
                    return None
                method = getattr(bambuPrinter, methodName, None)
                if method is None:
                    return None
                try:
                    return method()
                except Exception:
                    return None

            try:
                bambuPrinter = bambuApi.Printer(ipAddress, accessCode, serialNumber)
                mqttStart = getattr(bambuPrinter, "mqtt_start", None)
                connectMethod = getattr(bambuPrinter, "connect", None)
                if mqttStart:
                    mqttStart()
                elif connectMethod:
                    connectMethod()
                waitForMqttReady(bambuPrinter)

                telemetry["status"] = "Online"

                progressValue = self._parseOptionalFloat(safePrinterCall("get_percentage"))
                if progressValue is not None:
                    telemetry["progressPercent"] = progressValue

                remainingValue = safePrinterCall("get_time")
                remainingSeconds = self._parseOptionalInt(remainingValue)
                if remainingSeconds is not None:
                    telemetry["remainingTimeSeconds"] = remainingSeconds

                nozzleValue = self._parseOptionalFloat(safePrinterCall("get_nozzle_temperature"))
                if nozzleValue is not None:
                    telemetry["nozzleTemp"] = nozzleValue

                bedValue = self._parseOptionalFloat(safePrinterCall("get_bed_temperature"))
                if bedValue is not None:
                    telemetry["bedTemp"] = bedValue

                gcodeState = self._parseOptionalString(safePrinterCall("get_gcode_state"))
                if not gcodeState:
                    gcodeState = self._parseOptionalString(safePrinterCall("get_state"))
                if gcodeState:
                    telemetry["gcodeState"] = gcodeState

                return telemetry
            except Exception as error:  # noqa: BLE001 - telemetry is best-effort
                logging.debug("Unable to collect Bambu telemetry via API from %s: %s", ipAddress, error)
            finally:
                if bambuPrinter is not None:
                    stopMethod = getattr(bambuPrinter, "mqtt_stop", None)
                    if stopMethod is None:
                        stopMethod = getattr(bambuPrinter, "disconnect", None)
                    with contextlib.suppress(Exception):
                        if stopMethod:
                            stopMethod()

        if serialNumber and accessCode and mqtt is not None and looksLikeBambu:
            try:
                bambuTelemetry = self._fetchBambuTelemetry(ipAddress, serialNumber, accessCode)
                if bambuTelemetry:
                    telemetry.update(bambuTelemetry)
            except Exception as error:  # noqa: BLE001 - telemetry is best-effort
                logging.debug("Unable to fetch Bambu telemetry from %s: %s", ipAddress, error)

        return telemetry

    def _fetchBambuTelemetry(
        self,
        ipAddress: str,
        serialNumber: str,
        accessCode: str,
        timeoutSeconds: float = 4.0,
    ) -> Dict[str, Any]:
        if mqtt is None:  # pragma: no cover - guarded by caller
            return {}

        topicReport = f"device/{serialNumber}/report"
        topicRequest = f"device/{serialNumber}/request"
        receivedTelemetry: Dict[str, Any] = {}
        telemetryEvent = threading.Event()

        callbackApiVersion = getattr(mqtt, "CallbackAPIVersion", None)
        clientKwargs: Dict[str, Any] = {"protocol": mqtt.MQTTv311}  # type: ignore[attr-defined]
        if callbackApiVersion is not None:
            clientKwargs["callback_api_version"] = callbackApiVersion.VERSION2  # type: ignore[attr-defined]

        telemetryLock = threading.Lock()

        def mergeTelemetry(update: Dict[str, Any]) -> bool:
            significantKeys = {"progressPercent", "remainingTimeSeconds", "nozzleTemp", "bedTemp", "gcodeState"}
            hasChange = False
            hasDetails = False
            with telemetryLock:
                for key, value in update.items():
                    if key not in ("status", *significantKeys):
                        continue
                    if key != "status" and value is not None:
                        hasDetails = True
                    if receivedTelemetry.get(key) != value:
                        receivedTelemetry[key] = value
                        hasChange = True
            if hasChange and (hasDetails or receivedTelemetry.get("status")):
                return True
            return False

        def onConnect(
            client: mqtt.Client,  # type: ignore[name-defined]
            _userdata: Any,
            _flags: Dict[str, Any],
            reasonCode: Any,
            *extraArgs: Any,
        ) -> None:
            isFailure = False
            if getattr(reasonCode, "is_failure", None):
                isFailure = bool(reasonCode.is_failure)
            elif isinstance(reasonCode, int):
                isFailure = reasonCode != 0

            if isFailure:
                receivedTelemetry["status"] = "Offline"
                telemetryEvent.set()
                return
            client.subscribe(topicReport, qos=1)
            commandsToSend = [
                {"pushed": {"command": "get_status"}},
                {"pushed": {"command": "pushall"}},
                {"print": {"command": "getstate"}},
            ]
            for command in commandsToSend:
                try:
                    client.publish(topicRequest, json.dumps(command), qos=1)
                except Exception:  # noqa: BLE001 - telemetry is best-effort
                    continue

        def onMessage(
            _client: mqtt.Client,  # type: ignore[name-defined]
            _userdata: Any,
            message: Any,
        ) -> None:
            try:
                payload = json.loads(message.payload.decode("utf-8"))
            except Exception:  # noqa: BLE001 - ignore malformed payloads
                return

            def findKey(obj: Any, key: str) -> Any:
                if isinstance(obj, dict):
                    if key in obj:
                        return obj[key]
                    for nested in obj.values():
                        result = findKey(nested, key)
                        if result is not None:
                            return result
                elif isinstance(obj, list):
                    for nested in obj:
                        result = findKey(nested, key)
                        if result is not None:
                            return result
                return None

            statusPayload = {
                "mc_percent": findKey(payload, "mc_percent"),
                "gcode_state": findKey(payload, "gcode_state"),
                "mc_remaining_time": findKey(payload, "mc_remaining_time"),
                "nozzle_temper": findKey(payload, "nozzle_temper"),
                "bed_temper": findKey(payload, "bed_temper"),
            }
            if mergeTelemetry(self._interpretBambuStatus(statusPayload)):
                telemetryEvent.set()

        client = mqtt.Client(**clientKwargs)  # type: ignore[name-defined]
        client.username_pw_set("bblp", accessCode)
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.on_connect = onConnect
        client.on_message = onMessage

        client.loop_start()
        try:
            client.connect(ipAddress, 8883, keepalive=30)
            telemetryEvent.wait(timeoutSeconds)
        finally:
            client.loop_stop()
            with contextlib.suppress(Exception):
                client.disconnect()

        return receivedTelemetry

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
        self.base44Reporter.start()
        self._appendLogLine("Started listening...")
        self.startButton.config(state=tk.DISABLED)
        self.stopButton.config(state=tk.NORMAL)

    def stopListening(self) -> None:
        self.base44Reporter.stop()
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

        metadataForUpsert = metadata.get("unencryptedData") if isinstance(metadata.get("unencryptedData"), dict) else metadata
        upsertedRecord = upsertPrinterFromJobMetadata(metadataForUpsert)
        if upsertedRecord:
            printerConfig.update({key: value for key, value in upsertedRecord.items() if value not in (None, "")})
            self._applyUpsertedPrinterRecord(upsertedRecord)

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

        def resolveInt(key: str, default: Optional[int]) -> Optional[int]:
            for source in (metadata, printerConfig):
                if key in source:
                    interpreted = interpretInteger(source[key])
                    if interpreted is not None:
                        return interpreted
            return default

        ipAddressValue = resolveText("ipAddress") or printerConfig.get("ipAddress")
        serialValue = resolveText("serialNumber") or printerConfig.get("serialNumber")
        accessCodeValue = resolveText("accessCode") or printerConfig.get("accessCode")

        if not ipAddressValue or not accessCodeValue or not serialValue:
            self.log("Mangler LAN-informasjon for valgt printer – hopper over sending.")
            return

        lanStrategyValue = resolveText("lanStrategy") or str(printerConfig.get("lanStrategy") or "legacy")
        plateIndexValue = resolveInt("plateIndex", None)
        if plateIndexValue is None:
            plateIndexValue = 1
        waitSecondsValue = resolveInt("waitSeconds", None)
        if waitSecondsValue is None:
            waitSecondsValue = 8

        if path.suffix.lower() not in {".3mf", ".gcode"}:
            self.log(f"Kan ikke sende fil med ugyldig format: {path.name}")
            return

        assert path.suffix.lower() in {".3mf", ".gcode"}, f"Ugyldig inputtype: {path}"

        options = BambuPrintOptions(
            ipAddress=str(ipAddressValue),
            serialNumber=str(serialValue),
            accessCode=str(accessCodeValue),
            nickname=(
                (printerConfig.get("nickname") or printerConfig.get("printerName"))
                if isinstance(printerConfig, dict)
                else None
            ),
            useAms=resolveBool("useAms", True),
            bedLeveling=resolveBool("bedLeveling", True),
            layerInspect=resolveBool("layerInspect", True),
            flowCalibration=resolveBool("flowCalibration", False),
            vibrationCalibration=resolveBool("vibrationCalibration", False),
            secureConnection=resolveBool("secureConnection", False),
            lanStrategy=lanStrategyValue,
            plateIndex=plateIndexValue,
            waitSeconds=waitSecondsValue,
        )

        def worker() -> None:
            try:
                self.log(f"Sender til Bambu: {path}")
                sendBambuPrintJob(
                    filePath=path,
                    options=options,
                    statusConfig=printerConfig,
                    jobMetadata=metadata,
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
                    self.base44Reporter.stop()
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


def runGui() -> None:
    app = ListenerGuiApp()
    app.run()


if __name__ == "__main__":
    runGui()

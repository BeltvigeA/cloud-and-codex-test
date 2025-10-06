"""Simple GUI application for listening to channels and logging data to JSON."""

from __future__ import annotations

import contextlib
import json
import logging
import socket
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Callable, Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .client import (
    appendJsonLogEntry,
    configureLogging,
    defaultBaseUrl,
    defaultFilesDirectory,
    ensureOutputDirectory,
    listenForFiles,
)


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
        self.printers: list[Dict[str, str]] = self._loadPrinters()
        self.printerStatusQueue: "Queue[tuple[str, Any]]" = Queue()
        self.statusRefreshThread: Optional[threading.Thread] = None
        self.statusRefreshIntervalMs = 60_000
        self.pendingImmediateStatusRefresh = False

        self._buildLayout()
        self.root.after(200, self._processLogQueue)
        self.root.after(200, self._processPrinterStatusUpdates)
        self._scheduleStatusRefresh(0)

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
        ttk.Entry(parent, textvariable=self.recipientVar, width=30).grid(
            row=1, column=1, sticky=tk.EW, **paddingOptions
        )

        ttk.Label(parent, text="Output Directory:").grid(row=2, column=0, sticky=tk.W, **paddingOptions)
        self.outputDirVar = tk.StringVar(value=str(defaultFilesDirectory))
        outputDirFrame = ttk.Frame(parent)
        outputDirFrame.grid(row=2, column=1, sticky=tk.EW, **paddingOptions)
        outputDirEntry = ttk.Entry(outputDirFrame, textvariable=self.outputDirVar, width=40)
        outputDirEntry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(outputDirFrame, text="Browse", command=self._chooseOutputDir).pack(side=tk.LEFT, padx=4)

        ttk.Label(parent, text="JSON Log File:").grid(row=3, column=0, sticky=tk.W, **paddingOptions)
        self.logFileVar = tk.StringVar(
            value=str(Path.home() / ".printmaster" / "listener-log.json")
        )
        logFileFrame = ttk.Frame(parent)
        logFileFrame.grid(row=3, column=1, sticky=tk.EW, **paddingOptions)
        logFileEntry = ttk.Entry(logFileFrame, textvariable=self.logFileVar, width=40)
        logFileEntry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(logFileFrame, text="Browse", command=self._chooseLogFile).pack(side=tk.LEFT, padx=4)

        ttk.Label(parent, text="Poll Interval (seconds):").grid(row=4, column=0, sticky=tk.W, **paddingOptions)
        self.pollIntervalVar = tk.IntVar(value=30)
        ttk.Spinbox(parent, from_=5, to=3600, textvariable=self.pollIntervalVar).grid(
            row=4, column=1, sticky=tk.W, **paddingOptions
        )

        buttonFrame = ttk.Frame(parent)
        buttonFrame.grid(row=5, column=0, columnspan=2, pady=12)
        self.startButton = ttk.Button(buttonFrame, text="Start Listening", command=self.startListening)
        self.startButton.pack(side=tk.LEFT, padx=6)
        self.stopButton = ttk.Button(buttonFrame, text="Stop", command=self.stopListening, state=tk.DISABLED)
        self.stopButton.pack(side=tk.LEFT, padx=6)

        ttk.Label(parent, text="Event Log:").grid(row=6, column=0, sticky=tk.W, **paddingOptions)
        self.logText = tk.Text(parent, height=10, state=tk.DISABLED)
        self.logText.grid(row=6, column=1, sticky=tk.NSEW, **paddingOptions)

        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(6, weight=1)

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
        actionFrame.columnconfigure(0, weight=1)

        treeFrame = ttk.Frame(parent)
        treeFrame.grid(row=2, column=0, sticky=tk.NSEW, padx=8, pady=(4, 8))
        columns = ("nickname", "ipAddress", "accessCode", "serialNumber", "brand", "status")
        self.printerTree = ttk.Treeview(treeFrame, columns=columns, show="headings", selectmode="browse")
        self.printerTree.heading("nickname", text="Nickname")
        self.printerTree.heading("ipAddress", text="IP Address")
        self.printerTree.heading("accessCode", text="Access Code")
        self.printerTree.heading("serialNumber", text="Serial Number")
        self.printerTree.heading("brand", text="Brand")
        self.printerTree.heading("status", text="Status")
        self.printerTree.column("nickname", width=120)
        self.printerTree.column("ipAddress", width=110)
        self.printerTree.column("accessCode", width=110)
        self.printerTree.column("serialNumber", width=120)
        self.printerTree.column("brand", width=100)
        self.printerTree.column("status", width=100)

        scrollbar = ttk.Scrollbar(treeFrame, orient=tk.VERTICAL, command=self.printerTree.yview)
        self.printerTree.configure(yscrollcommand=scrollbar.set)
        self.printerTree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.printerTree.bind("<<TreeviewSelect>>", self._onPrinterSelection)

        self._refreshPrinterList()

    def _loadPrinters(self) -> list[Dict[str, str]]:
        try:
            self.printerStoragePath.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            logging.warning("Unable to prepare printer storage directory: %s", error)
        if self.printerStoragePath.exists():
            try:
                with self.printerStoragePath.open("r", encoding="utf-8") as printerFile:
                    loadedPrinters = json.load(printerFile)
                if isinstance(loadedPrinters, list):
                    sanitizedPrinters: list[Dict[str, str]] = []
                    for entry in loadedPrinters:
                        if isinstance(entry, dict):
                            sanitizedPrinters.append(
                                {
                                    "nickname": str(entry.get("nickname", "")),
                                    "ipAddress": str(entry.get("ipAddress", "")),
                                    "accessCode": str(entry.get("accessCode", "")),
                                    "serialNumber": str(entry.get("serialNumber", "")),
                                    "brand": str(entry.get("brand", "")),
                                    "status": str(entry.get("status", "")) or "Unknown",
                                }
                            )
                    return sanitizedPrinters
            except (OSError, json.JSONDecodeError) as error:
                logging.warning("Unable to load printers from %s: %s", self.printerStoragePath, error)
        return []

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
                ),
            )
        self._onPrinterSelection(None)

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
        initialValues: Optional[Dict[str, str]],
        onSave: Callable[[Dict[str, str]], None],
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
        initialStatus = (initialValues or {}).get("status", "Unknown") or "Unknown"
        statusChoices = list(self.printerStatusOptions)
        if initialStatus not in statusChoices:
            statusChoices.append(initialStatus)
        statusVar = tk.StringVar(value=initialStatus)

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

        ttk.Label(dialog, text="Status:").grid(row=5, column=0, sticky=tk.W, padx=12, pady=4)
        statusCombo = ttk.Combobox(
            dialog,
            textvariable=statusVar,
            values=tuple(statusChoices),
            state="readonly",
        )
        statusCombo.grid(row=5, column=1, sticky=tk.EW, padx=12, pady=4)

        buttonFrame = ttk.Frame(dialog)
        buttonFrame.grid(row=6, column=0, columnspan=2, pady=12)
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
                statusVar,
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
        statusVar: tk.StringVar,
        onSave: Callable[[Dict[str, str]], None],
    ) -> None:
        nickname = nicknameVar.get().strip()
        ipAddress = ipAddressVar.get().strip()
        accessCode = accessCodeVar.get().strip()
        serialNumber = serialNumberVar.get().strip()
        brand = brandVar.get().strip()
        status = statusVar.get().strip() or "Unknown"

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
            "status": status,
        }

        onSave(printerDetails)
        dialog.destroy()

    def _handleCreatePrinter(self, printerDetails: Dict[str, str]) -> None:
        self.printers.append(printerDetails)
        self._savePrinters()
        self._refreshPrinterList()
        self._scheduleStatusRefresh(0)

    def _handleUpdatePrinter(self, index: int, printerDetails: Dict[str, str]) -> None:
        self.printers[index] = printerDetails
        self._savePrinters()
        self._refreshPrinterList()
        self._scheduleStatusRefresh(0)

    def _onPrinterSelection(self, event: object) -> None:  # noqa: ARG002 - required by Tk callback
        state = tk.NORMAL if self._getSelectedPrinterIndex() is not None else tk.DISABLED
        self.editPrinterButton.config(state=state)

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
        updates: list[tuple[int, str]] = []
        printersSnapshot = list(enumerate(list(self.printers)))
        for index, printer in printersSnapshot:
            ipAddress = printer.get("ipAddress", "").strip()
            if not ipAddress:
                continue
            detectedStatus = self._probePrinterStatus(ipAddress)
            currentStatus = printer.get("status", "Unknown") or "Unknown"
            if detectedStatus != currentStatus:
                logging.info(
                    "Printer %s status changed from %s to %s",
                    ipAddress,
                    currentStatus,
                    detectedStatus,
                )
                updates.append((index, detectedStatus))
        if updates:
            self.printerStatusQueue.put(("updates", updates))
        self.printerStatusQueue.put(("complete", None))

    def _processPrinterStatusUpdates(self) -> None:
        try:
            while True:
                messageType, payload = self.printerStatusQueue.get_nowait()
                if messageType == "updates":
                    updatesPayload: list[tuple[int, str]] = (
                        payload if isinstance(payload, list) else []
                    )
                    hasChanges = False
                    for index, status in updatesPayload:
                        if 0 <= index < len(self.printers):
                            self.printers[index]["status"] = status
                            hasChanges = True
                    if hasChanges:
                        self._savePrinters()
                        self._refreshPrinterList()
                elif messageType == "complete":
                    self.statusRefreshThread = None
                    delay = 0 if self.pendingImmediateStatusRefresh else self.statusRefreshIntervalMs
                    self._scheduleStatusRefresh(delay)
        except Empty:
            pass
        self.root.after(500, self._processPrinterStatusUpdates)

    def _probePrinterStatus(self, ipAddress: str, timeoutSeconds: float = 2.0) -> str:
        portsToTry = (8883, 443, 80)
        for port in portsToTry:
            try:
                with contextlib.closing(socket.create_connection((ipAddress, port), timeoutSeconds)):
                    return "Online"
            except (OSError, ValueError):
                continue
        return "Offline"

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
        self._appendLogLine("Started listening...")
        self.startButton.config(state=tk.DISABLED)
        self.stopButton.config(state=tk.NORMAL)

    def stopListening(self) -> None:
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


def runGui() -> None:
    app = ListenerGuiApp()
    app.run()


if __name__ == "__main__":
    runGui()

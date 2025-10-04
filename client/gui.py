"""Simple GUI application for listening to channels and logging data to JSON."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Dict, Optional

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


class PrinterWizard(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        initialDetails: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(parent)
        self.title("Printer Wizard")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._onCancel)

        details = initialDetails or {}
        self.nicknameVar = tk.StringVar(value=details.get("nickname", ""))
        self.brandVar = tk.StringVar(value=details.get("brand", ""))
        self.ipAddressVar = tk.StringVar(value=details.get("ipAddress", ""))
        self.accessCodeVar = tk.StringVar(value=details.get("accessCode", ""))
        self.serialNumberVar = tk.StringVar(value=details.get("serialNumber", ""))

        self.result: Optional[Dict[str, str]] = None
        self.currentStep = 0

        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        self.stepTitleVar = tk.StringVar()
        ttk.Label(container, textvariable=self.stepTitleVar, font=("TkDefaultFont", 12, "bold")).pack(
            anchor=tk.W, pady=(0, 8)
        )

        self.stepFrames: list[ttk.Frame] = []
        self.stepTitles = [
            "Basic Information",
            "Connection Details",
            "Optional Extras",
        ]

        self._buildBasicStep(container)
        self._buildConnectionStep(container)
        self._buildExtrasStep(container)

        self.navigationFrame = ttk.Frame(self)
        self.navigationFrame.pack(fill=tk.X, padx=16, pady=(0, 16))

        self.backButton = ttk.Button(self.navigationFrame, text="Back", command=self._onBack)
        self.backButton.pack(side=tk.LEFT)

        self.nextButton = ttk.Button(self.navigationFrame, text="Next", command=self._onNext)
        self.nextButton.pack(side=tk.LEFT, padx=8)

        self.finishButton = ttk.Button(
            self.navigationFrame,
            text="Finish",
            command=self._onFinish,
        )
        self.finishButton.pack(side=tk.LEFT)

        ttk.Button(self.navigationFrame, text="Cancel", command=self._onCancel).pack(side=tk.RIGHT)

        self._showStep(0)
        self.focus_set()

    def _buildBasicStep(self, container: ttk.Frame) -> None:
        frame = ttk.Frame(container)
        ttk.Label(frame, text="Nickname:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.nicknameEntry = ttk.Entry(frame, textvariable=self.nicknameVar, width=32)
        self.nicknameEntry.grid(row=0, column=1, sticky=tk.EW, pady=4)

        ttk.Label(frame, text="Brand:").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.brandVar, width=32).grid(row=1, column=1, sticky=tk.EW, pady=4)

        frame.columnconfigure(1, weight=1)
        self.stepFrames.append(frame)

    def _buildConnectionStep(self, container: ttk.Frame) -> None:
        frame = ttk.Frame(container)
        ttk.Label(frame, text="IP Address:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.ipAddressEntry = ttk.Entry(frame, textvariable=self.ipAddressVar, width=32)
        self.ipAddressEntry.grid(row=0, column=1, sticky=tk.EW, pady=4)

        ttk.Label(frame, text="Access Code:").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.accessCodeVar, width=32).grid(row=1, column=1, sticky=tk.EW, pady=4)

        frame.columnconfigure(1, weight=1)
        self.stepFrames.append(frame)

    def _buildExtrasStep(self, container: ttk.Frame) -> None:
        frame = ttk.Frame(container)
        ttk.Label(frame, text="Serial Number:").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self.serialNumberVar, width=32).grid(
            row=0,
            column=1,
            sticky=tk.EW,
            pady=4,
        )

        frame.columnconfigure(1, weight=1)
        self.stepFrames.append(frame)

    def _showStep(self, index: int) -> None:
        index = max(0, min(index, len(self.stepFrames) - 1))
        for frame in self.stepFrames:
            frame.pack_forget()
        self.stepFrames[index].pack(fill=tk.BOTH, expand=True)
        self.stepTitleVar.set(self.stepTitles[index])
        self.currentStep = index

        self.backButton.config(state=tk.NORMAL if index > 0 else tk.DISABLED)
        if index >= len(self.stepFrames) - 1:
            self.nextButton.config(state=tk.DISABLED)
            self.finishButton.config(state=tk.NORMAL)
        else:
            self.nextButton.config(state=tk.NORMAL)
            self.finishButton.config(state=tk.DISABLED)

        if index == 0:
            self.nicknameEntry.focus_set()
        elif index == 1:
            self.ipAddressEntry.focus_set()

    def _onBack(self) -> None:
        self._showStep(self.currentStep - 1)

    def _onNext(self) -> None:
        self._showStep(self.currentStep + 1)

    def _collectResult(self) -> Optional[Dict[str, str]]:
        nickname = self.nicknameVar.get().strip()
        if not nickname:
            messagebox.showerror("Printer Wizard", "Nickname is required.")
            self._showStep(0)
            self.nicknameEntry.focus_set()
            return None

        ipAddress = self.ipAddressVar.get().strip()
        if not ipAddress:
            messagebox.showerror("Printer Wizard", "IP address is required.")
            self._showStep(1)
            self.ipAddressEntry.focus_set()
            return None

        return {
            "nickname": nickname,
            "ipAddress": ipAddress,
            "accessCode": self.accessCodeVar.get().strip(),
            "serialNumber": self.serialNumberVar.get().strip(),
            "brand": self.brandVar.get().strip(),
        }

    def _onFinish(self) -> None:
        result = self._collectResult()
        if result is None:
            return
        self.result = result
        self.destroy()

    def _onCancel(self) -> None:
        self.result = None
        self.destroy()

    def show(self) -> Optional[Dict[str, str]]:
        self.wait_window()
        return self.result


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
        self.selectedPrinterIndex: Optional[int] = None

        self._buildLayout()
        self.root.after(200, self._processLogQueue)

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
        self.printerNicknameVar = tk.StringVar()
        self.printerIpAddressVar = tk.StringVar()
        self.printerAccessCodeVar = tk.StringVar()
        self.printerSerialNumberVar = tk.StringVar()
        self.printerBrandVar = tk.StringVar()
        self.printerSearchVar = tk.StringVar()

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        searchFrame = ttk.Frame(parent)
        searchFrame.grid(row=0, column=0, sticky=tk.EW, padx=8, pady=(8, 4))
        ttk.Label(searchFrame, text="Search by Name or IP:").pack(side=tk.LEFT)
        searchEntry = ttk.Entry(searchFrame, textvariable=self.printerSearchVar, width=30)
        searchEntry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(searchFrame, text="Clear", command=self._clearPrinterSearch).pack(side=tk.LEFT)
        self.printerSearchVar.trace_add("write", lambda *_: self._refreshPrinterList())

        formFrame = ttk.LabelFrame(parent, text="Printer Details")
        formFrame.grid(row=1, column=0, sticky=tk.EW, padx=8, pady=4)
        formFrame.columnconfigure(1, weight=1)

        ttk.Label(formFrame, text="Nickname:").grid(row=0, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(formFrame, textvariable=self.printerNicknameVar).grid(
            row=0, column=1, sticky=tk.EW, padx=6, pady=4
        )

        ttk.Label(formFrame, text="IP Address:").grid(row=1, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(formFrame, textvariable=self.printerIpAddressVar).grid(
            row=1, column=1, sticky=tk.EW, padx=6, pady=4
        )

        ttk.Label(formFrame, text="Access Code:").grid(row=2, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(formFrame, textvariable=self.printerAccessCodeVar).grid(
            row=2, column=1, sticky=tk.EW, padx=6, pady=4
        )

        ttk.Label(formFrame, text="Serial Number:").grid(row=3, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(formFrame, textvariable=self.printerSerialNumberVar).grid(
            row=3, column=1, sticky=tk.EW, padx=6, pady=4
        )

        ttk.Label(formFrame, text="Brand:").grid(row=4, column=0, sticky=tk.W, padx=6, pady=4)
        ttk.Entry(formFrame, textvariable=self.printerBrandVar).grid(
            row=4, column=1, sticky=tk.EW, padx=6, pady=4
        )

        actionFrame = ttk.Frame(formFrame)
        actionFrame.grid(row=5, column=0, columnspan=2, pady=6)
        self.savePrinterButton = ttk.Button(
            actionFrame,
            text="Add Printer",
            command=self._openPrinterWizard,
        )
        self.savePrinterButton.pack(side=tk.LEFT, padx=4)
        ttk.Button(actionFrame, text="Clear Selection", command=self._clearPrinterSelection).pack(
            side=tk.LEFT, padx=4
        )

        treeFrame = ttk.Frame(parent)
        treeFrame.grid(row=2, column=0, sticky=tk.NSEW, padx=8, pady=(4, 8))
        columns = ("nickname", "ipAddress", "accessCode", "serialNumber", "brand")
        self.printerTree = ttk.Treeview(treeFrame, columns=columns, show="headings", selectmode="browse")
        self.printerTree.heading("nickname", text="Nickname")
        self.printerTree.heading("ipAddress", text="IP Address")
        self.printerTree.heading("accessCode", text="Access Code")
        self.printerTree.heading("serialNumber", text="Serial Number")
        self.printerTree.heading("brand", text="Brand")
        self.printerTree.column("nickname", width=120)
        self.printerTree.column("ipAddress", width=110)
        self.printerTree.column("accessCode", width=110)
        self.printerTree.column("serialNumber", width=120)
        self.printerTree.column("brand", width=100)

        scrollbar = ttk.Scrollbar(treeFrame, orient=tk.VERTICAL, command=self.printerTree.yview)
        self.printerTree.configure(yscrollcommand=scrollbar.set)
        self.printerTree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.printerTree.bind("<<TreeviewSelect>>", self._onPrinterSelect)

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
        visibleIds: list[str] = []
        for index, printer in enumerate(self.printers):
            nickname = printer.get("nickname", "")
            ipAddress = printer.get("ipAddress", "")
            if searchTerm and searchTerm not in nickname.lower() and searchTerm not in ipAddress.lower():
                continue
            itemId = str(index)
            visibleIds.append(itemId)
            self.printerTree.insert(
                "",
                tk.END,
                iid=itemId,
                values=(
                    nickname,
                    ipAddress,
                    printer.get("accessCode", ""),
                    printer.get("serialNumber", ""),
                    printer.get("brand", ""),
                ),
            )
        if self.selectedPrinterIndex is not None:
            selectedId = str(self.selectedPrinterIndex)
            if selectedId in visibleIds:
                self.printerTree.selection_set(selectedId)
            else:
                self._clearPrinterSelection()

    def _clearPrinterSearch(self) -> None:
        self.printerSearchVar.set("")

    def _openPrinterWizard(self) -> None:
        initialDetails = {
            "nickname": self.printerNicknameVar.get().strip(),
            "ipAddress": self.printerIpAddressVar.get().strip(),
            "accessCode": self.printerAccessCodeVar.get().strip(),
            "serialNumber": self.printerSerialNumberVar.get().strip(),
            "brand": self.printerBrandVar.get().strip(),
        }

        if (
            self.selectedPrinterIndex is not None
            and 0 <= self.selectedPrinterIndex < len(self.printers)
        ):
            storedDetails = self.printers[self.selectedPrinterIndex]
            for key, value in storedDetails.items():
                if not initialDetails.get(key):
                    initialDetails[key] = value

        wizard = PrinterWizard(self.root, initialDetails)
        printerDetails = wizard.show()
        if printerDetails:
            self._savePrinterDetails(printerDetails)

    def _savePrinterDetails(self, printerDetails: Dict[str, str]) -> None:
        isEditing = (
            self.selectedPrinterIndex is not None
            and 0 <= self.selectedPrinterIndex < len(self.printers)
        )

        if isEditing and self.selectedPrinterIndex is not None:
            self.printers[self.selectedPrinterIndex] = printerDetails
        else:
            self.printers.append(printerDetails)

        self._savePrinters()
        self._refreshPrinterList()

        if isEditing and self.selectedPrinterIndex is not None:
            self.printerNicknameVar.set(printerDetails.get("nickname", ""))
            self.printerIpAddressVar.set(printerDetails.get("ipAddress", ""))
            self.printerAccessCodeVar.set(printerDetails.get("accessCode", ""))
            self.printerSerialNumberVar.set(printerDetails.get("serialNumber", ""))
            self.printerBrandVar.set(printerDetails.get("brand", ""))
            self.savePrinterButton.config(text="Update Printer")
            if hasattr(self, "printerTree"):
                itemId = str(self.selectedPrinterIndex)
                if self.printerTree.exists(itemId):
                    self.printerTree.selection_set(itemId)
                    self.printerTree.focus(itemId)
                    self.printerTree.see(itemId)
        else:
            self._clearPrinterSelection()

    def _clearPrinterSelection(self) -> None:
        if hasattr(self, "printerTree"):
            self.printerTree.selection_remove(self.printerTree.selection())
        self.selectedPrinterIndex = None
        self.printerNicknameVar.set("")
        self.printerIpAddressVar.set("")
        self.printerAccessCodeVar.set("")
        self.printerSerialNumberVar.set("")
        self.printerBrandVar.set("")
        self.savePrinterButton.config(text="Add Printer")

    def _onPrinterSelect(self, _event: tk.Event) -> None:
        selection = self.printerTree.selection()
        if not selection:
            return
        selectedId = selection[0]
        try:
            self.selectedPrinterIndex = int(selectedId)
        except ValueError:
            self.selectedPrinterIndex = None
            return
        if self.selectedPrinterIndex is None or self.selectedPrinterIndex >= len(self.printers):
            return
        printer = self.printers[self.selectedPrinterIndex]
        self.printerNicknameVar.set(printer.get("nickname", ""))
        self.printerIpAddressVar.set(printer.get("ipAddress", ""))
        self.printerAccessCodeVar.set(printer.get("accessCode", ""))
        self.printerSerialNumberVar.set(printer.get("serialNumber", ""))
        self.printerBrandVar.set(printer.get("brand", ""))
        self.savePrinterButton.config(text="Update Printer")

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

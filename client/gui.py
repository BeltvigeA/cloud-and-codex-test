"""Simple GUI application for listening to channels and logging data to JSON."""

from __future__ import annotations

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

        self._buildLayout()
        self.root.after(200, self._processLogQueue)

    def _buildLayout(self) -> None:
        paddingOptions = {"padx": 8, "pady": 4}

        mainFrame = ttk.Frame(self.root)
        mainFrame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(mainFrame, text="Base URL:").grid(row=0, column=0, sticky=tk.W, **paddingOptions)
        self.baseUrlVar = tk.StringVar(value=defaultBaseUrl)
        ttk.Entry(mainFrame, textvariable=self.baseUrlVar, width=50).grid(
            row=0, column=1, sticky=tk.EW, **paddingOptions
        )

        ttk.Label(mainFrame, text="Channel (Recipient ID):").grid(
            row=1, column=0, sticky=tk.W, **paddingOptions
        )
        self.recipientVar = tk.StringVar()
        ttk.Entry(mainFrame, textvariable=self.recipientVar, width=30).grid(
            row=1, column=1, sticky=tk.EW, **paddingOptions
        )

        ttk.Label(mainFrame, text="Output Directory:").grid(
            row=2, column=0, sticky=tk.W, **paddingOptions
        )
        self.outputDirVar = tk.StringVar(value=str(Path.home() / "cloud-and-codex-test"))
        outputDirFrame = ttk.Frame(mainFrame)
        outputDirFrame.grid(row=2, column=1, sticky=tk.EW, **paddingOptions)
        outputDirEntry = ttk.Entry(outputDirFrame, textvariable=self.outputDirVar, width=40)
        outputDirEntry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(outputDirFrame, text="Browse", command=self._chooseOutputDir).pack(side=tk.LEFT, padx=4)

        ttk.Label(mainFrame, text="JSON Log File:").grid(row=3, column=0, sticky=tk.W, **paddingOptions)
        self.logFileVar = tk.StringVar(value=str(Path.home() / "cloud-and-codex-test" / "listener-log.json"))
        logFileFrame = ttk.Frame(mainFrame)
        logFileFrame.grid(row=3, column=1, sticky=tk.EW, **paddingOptions)
        logFileEntry = ttk.Entry(logFileFrame, textvariable=self.logFileVar, width=40)
        logFileEntry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(logFileFrame, text="Browse", command=self._chooseLogFile).pack(side=tk.LEFT, padx=4)

        ttk.Label(mainFrame, text="Poll Interval (seconds):").grid(
            row=4, column=0, sticky=tk.W, **paddingOptions
        )
        self.pollIntervalVar = tk.IntVar(value=30)
        ttk.Spinbox(mainFrame, from_=5, to=3600, textvariable=self.pollIntervalVar).grid(
            row=4, column=1, sticky=tk.W, **paddingOptions
        )

        buttonFrame = ttk.Frame(mainFrame)
        buttonFrame.grid(row=5, column=0, columnspan=2, pady=12)
        self.startButton = ttk.Button(buttonFrame, text="Start Listening", command=self.startListening)
        self.startButton.pack(side=tk.LEFT, padx=6)
        self.stopButton = ttk.Button(buttonFrame, text="Stop", command=self.stopListening, state=tk.DISABLED)
        self.stopButton.pack(side=tk.LEFT, padx=6)

        ttk.Label(mainFrame, text="Event Log:").grid(row=6, column=0, sticky=tk.W, **paddingOptions)
        self.logText = tk.Text(mainFrame, height=10, state=tk.DISABLED)
        self.logText.grid(row=6, column=1, sticky=tk.NSEW, **paddingOptions)

        mainFrame.columnconfigure(1, weight=1)
        mainFrame.rowconfigure(6, weight=1)

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
            )
        except Exception as error:  # noqa: BLE001 - surface exceptions to the GUI
            logging.exception("Listener encountered an error: %s", error)
            self.logQueue.put(f"Error: {error}")
        finally:
            self.logQueue.put("__LISTENER_STOPPED__")

    def _handleFetchedData(self, details: Dict[str, object]) -> None:
        logMessage = f"Fetched file: {details.get('savedFile')}"
        if self.logFilePath is not None:
            try:
                appendJsonLogEntry(self.logFilePath, details)
                logMessage += f" | Metadata saved to {self.logFilePath}"
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

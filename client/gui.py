"""Lightweight Tkinter GUI for capturing 3D printer job metadata."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

import tkinter as tk
from tkinter import filedialog, messagebox


@dataclass
class JobMetadata:
    base64ImageCode: str = ""
    filamentColor: str = ""
    filamentType: str = ""
    filamentBrand: str = ""
    filamentDiameter: float | None = None
    infillDensity: int | None = None
    layerHeight: float | None = None
    nozzleSize: float | None = None
    orientation: str = ""
    printJobId: str = ""
    slicedOn: str = ""
    slicedSettings: str = ""
    stlFileName: str = ""
    top3dPrinter: str = ""


numericIntegerFields = {"infillDensity"}
numericFloatFields = {"filamentDiameter", "layerHeight", "nozzleSize"}


def parseFieldValue(fieldName: str, rawValue: str) -> Any:
    sanitizedValue = rawValue.strip()
    if not sanitizedValue:
        return None

    if fieldName in numericIntegerFields:
        try:
            return int(sanitizedValue)
        except ValueError as error:
            raise ValueError(f"{fieldName} must be an integer") from error

    if fieldName in numericFloatFields:
        try:
            return float(sanitizedValue)
        except ValueError as error:
            raise ValueError(f"{fieldName} must be a number") from error

    return sanitizedValue


class JobMetadataForm:
    def __init__(self) -> None:
        self.rootWindow = tk.Tk()
        self.rootWindow.title("PrintMaster Metadata Collector")
        self.entryWidgets: Dict[str, tk.Entry] = {}
        self.statusVar = tk.StringVar(value="Fill in the form and press Save JSON to export metadata.")

        self._buildLayout()

    def _buildLayout(self) -> None:
        self.rootWindow.columnconfigure(0, weight=1)

        headerLabel = tk.Label(
            self.rootWindow,
            text="Print job metadata",
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        )
        headerLabel.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        formFrame = tk.Frame(self.rootWindow)
        formFrame.grid(row=1, column=0, sticky="nsew", padx=12)
        formFrame.columnconfigure(1, weight=1)

        fieldDefinitions = [
            ("base64ImageCode", "Base64 image code"),
            ("filamentColor", "Filament color"),
            ("filamentType", "Filament type"),
            ("filamentBrand", "Filament brand"),
            ("filamentDiameter", "Filament diameter (mm)"),
            ("infillDensity", "Infill density (%)"),
            ("layerHeight", "Layer height (mm)"),
            ("nozzleSize", "Nozzle size (mm)"),
            ("orientation", "Orientation"),
            ("printJobId", "Print job ID"),
            ("slicedOn", "Sliced on"),
            ("slicedSettings", "Sliced settings"),
            ("stlFileName", "STL file name"),
            ("top3dPrinter", "3D printer"),
        ]

        for rowIndex, (fieldName, labelText) in enumerate(fieldDefinitions):
            labelWidget = tk.Label(formFrame, text=labelText, anchor="w")
            labelWidget.grid(row=rowIndex, column=0, sticky="w", pady=3)

            entryWidget = tk.Entry(formFrame)
            entryWidget.grid(row=rowIndex, column=1, sticky="ew", pady=3)
            self.entryWidgets[fieldName] = entryWidget

        buttonFrame = tk.Frame(self.rootWindow)
        buttonFrame.grid(row=2, column=0, sticky="ew", padx=12, pady=(12, 0))
        buttonFrame.columnconfigure(0, weight=1)
        buttonFrame.columnconfigure(1, weight=1)
        buttonFrame.columnconfigure(2, weight=1)

        loadButton = tk.Button(buttonFrame, text="Load JSON", command=self.handleLoadJson)
        loadButton.grid(row=0, column=0, padx=4, sticky="ew")

        saveButton = tk.Button(buttonFrame, text="Save JSON", command=self.handleSaveJson)
        saveButton.grid(row=0, column=1, padx=4, sticky="ew")

        clearButton = tk.Button(buttonFrame, text="Clear", command=self.handleClear)
        clearButton.grid(row=0, column=2, padx=4, sticky="ew")

        self.statusLabel = tk.Label(
            self.rootWindow,
            textvariable=self.statusVar,
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.statusLabel.grid(row=3, column=0, sticky="ew", padx=12, pady=(10, 12))

    def handleLoadJson(self) -> None:
        filePath = filedialog.askopenfilename(
            title="Load metadata", filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filePath:
            return

        try:
            metadata = json.loads(Path(filePath).read_text(encoding="utf-8"))
        except OSError as error:
            messagebox.showerror("Unable to read file", str(error))
            return
        except json.JSONDecodeError as error:
            messagebox.showerror("Invalid JSON file", str(error))
            return

        self.populateForm(metadata)
        self.statusVar.set(f"Loaded metadata from {filePath}.")

    def handleSaveJson(self) -> None:
        try:
            metadata = self.collectFormData()
        except ValueError as error:
            messagebox.showerror("Invalid data", str(error))
            return

        filePath = filedialog.asksaveasfilename(
            title="Save metadata",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not filePath:
            return

        try:
            Path(filePath).write_text(json.dumps(asdict(metadata), indent=2), encoding="utf-8")
        except OSError as error:
            messagebox.showerror("Unable to save file", str(error))
            return

        self.statusVar.set(f"Saved metadata to {filePath}.")

    def handleClear(self) -> None:
        for entryWidget in self.entryWidgets.values():
            entryWidget.delete(0, tk.END)
        self.statusVar.set("Form cleared.")

    def populateForm(self, metadata: Dict[str, Any]) -> None:
        for fieldName, entryWidget in self.entryWidgets.items():
            value = metadata.get(fieldName)
            entryWidget.delete(0, tk.END)
            if value is None:
                continue
            entryWidget.insert(0, str(value))

    def collectFormData(self) -> JobMetadata:
        metadata = JobMetadata()
        for fieldName, entryWidget in self.entryWidgets.items():
            rawValue = entryWidget.get()
            value = parseFieldValue(fieldName, rawValue)
            setattr(metadata, fieldName, value)
        return metadata

    def launch(self) -> None:
        self.rootWindow.mainloop()


def launchGui() -> None:
    guiApp = JobMetadataForm()
    guiApp.launch()


if __name__ == "__main__":
    launchGui()

"""Tkinter page for displaying structured client logs."""

from __future__ import annotations

import json
import time
import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from .json_viewer import showJsonViewer
from .logbus import BUS, CATEGORIES, LogEvent


class LogsPage(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self._categoryVariables: Dict[str, tk.BooleanVar] = {}
        self._itemEvents: Dict[str, LogEvent] = {}

        self.toolbar = ttk.Frame(self)
        self.toolbar.pack(fill=tk.X)

        for category in CATEGORIES:
            defaultValue = category != "conn-error"
            variable = tk.BooleanVar(value=defaultValue)
            self._categoryVariables[category] = variable
            ttk.Checkbutton(
                self.toolbar,
                text=category,
                variable=variable,
                command=self.refresh,
            ).pack(side=tk.LEFT, padx=4)

        self._searchValue = tk.StringVar()
        ttk.Entry(self.toolbar, textvariable=self._searchValue, width=28).pack(side=tk.RIGHT, padx=6)
        ttk.Label(self.toolbar, text="Search").pack(side=tk.RIGHT)

        self.copyAllLogsButton = ttk.Button(
            self.toolbar,
            text="Copy all logs",
            command=self.copyAllLogsToClipboard,
        )
        self.copyAllLogsButton.pack(side=tk.RIGHT, padx=4)

        self.copySelectedLogButton = ttk.Button(
            self.toolbar,
            text="Copy selected log",
            command=self.copySelectedLogToClipboard,
        )
        self.copySelectedLogButton.pack(side=tk.RIGHT, padx=4)

        self._scrollButton = ttk.Button(self.toolbar, text="Scroll to bottom", command=self._scrollToBottom)
        self._scrollButton.pack(side=tk.RIGHT, padx=4)

        self._clearButton = ttk.Button(self.toolbar, text="Clear", command=self._handleClear)
        self._clearButton.pack(side=tk.RIGHT, padx=4)

        self._tree = ttk.Treeview(
            self,
            columns=("time", "level", "category", "event", "message"),
            show="headings",
            height=20,
        )
        for column, width in (
            ("time", 160),
            ("level", 70),
            ("category", 120),
            ("event", 160),
            ("message", 800),
        ):
            self._tree.heading(column, text=column)
            self._tree.column(column, width=width, anchor=tk.W)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.bind("<Double-1>", self._openDetails)
        self._tree.bind("<<TreeviewSelect>>", self._handleSelection)

        self._selectedEventKey: str | None = None

        self.after(500, self.refresh)

    def _filteredRows(self) -> List[LogEvent]:
        searchFilter = self._searchValue.get().strip().lower()
        enabledCategories = {
            category
            for category, variable in self._categoryVariables.items()
            if variable.get()
        }
        events = BUS.snapshot()[-2000:]
        rows: List[LogEvent] = []
        for event in events:
            if enabledCategories and event.category not in enabledCategories:
                continue
            if searchFilter:
                contextText = json.dumps(event.ctx, ensure_ascii=False)
                combined = f"{event.message} {contextText}".lower()
                if searchFilter not in combined:
                    continue
            rows.append(event)
        return rows

    def _makeEventKey(self, event: LogEvent) -> str:
        contextText = json.dumps(event.ctx, ensure_ascii=False, sort_keys=True)
        message = event.message or ""
        return "|".join(
            [
                f"{event.ts}",
                event.level,
                event.category,
                event.event,
                message,
                contextText,
            ]
        )

    def refresh(self) -> None:
        rows = self._filteredRows()
        selectedKey = self._selectedEventKey

        selection = self._tree.selection()
        if selection:
            previousEvent = self._itemEvents.get(selection[0])
            if previousEvent:
                selectedKey = self._makeEventKey(previousEvent)

        self._itemEvents.clear()

        for item in self._tree.get_children():
            self._tree.delete(item)

        restoredItem: str | None = None

        for event in rows:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(event.ts))
            contextText = json.dumps(event.ctx, ensure_ascii=False)
            message = event.message or ""
            values = (timestamp, event.level, event.category, event.event, f"{message} {contextText}".strip())
            itemId = self._tree.insert("", "end", values=values)
            self._itemEvents[itemId] = event
            if selectedKey and restoredItem is None and self._makeEventKey(event) == selectedKey:
                restoredItem = itemId

        if restoredItem:
            self._tree.selection_set(restoredItem)
            self._tree.see(restoredItem)

        self.after(1000, self.refresh)

    def _openDetails(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        itemId = selection[0]
        event = self._itemEvents.get(itemId)
        if not event:
            return
        showJsonViewer(
            self,
            f"{event.category} · {event.event}",
            {
                "ts": event.ts,
                "level": event.level,
                "category": event.category,
                "event": event.event,
                "message": event.message,
                "ctx": event.ctx,
            },
        )

    def _handleClear(self) -> None:
        for category, variable in self._categoryVariables.items():
            if variable.get():
                BUS.clear(category=category)
        self.refresh()

    def _scrollToBottom(self) -> None:
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])

    def _handleSelection(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self._tree.selection()
        if not selection:
            self._selectedEventKey = None
            return
        event = self._itemEvents.get(selection[0])
        self._selectedEventKey = self._makeEventKey(event) if event else None

    def copyAllLogsToClipboard(self) -> None:
        rows = self._filteredRows()
        if not rows:
            return
        serialized = [json.dumps(self._serializeEvent(row), ensure_ascii=False) for row in rows]
        combined = "\n".join(serialized)
        self.clipboard_clear()
        self.clipboard_append(combined)

    def copySelectedLogToClipboard(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        event = self._itemEvents.get(selection[0])
        if not event:
            return
        serialized = json.dumps(self._serializeEvent(event), ensure_ascii=False)
        self.clipboard_clear()
        self.clipboard_append(serialized)

    def _serializeEvent(self, event: LogEvent) -> dict:
        return {
            "ts": event.ts,
            "level": event.level,
            "category": event.category,
            "event": event.event,
            "message": event.message,
            "ctx": event.ctx,
        }


__all__ = ["LogsPage"]

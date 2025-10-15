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
        self._selectedEvent: LogEvent | None = None
        self._selectedEventKey: tuple | None = None

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

        self._scrollButton = ttk.Button(self.toolbar, text="Scroll to bottom", command=self._scrollToBottom)
        self._scrollButton.pack(side=tk.RIGHT, padx=4)

        self._clearButton = ttk.Button(self.toolbar, text="Clear", command=self._handleClear)
        self._clearButton.pack(side=tk.RIGHT, padx=4)

        self._copyLogButton = ttk.Button(
            self.toolbar,
            text="Copy log",
            command=self._copyLogToClipboard,
        )
        self._copyLogButton.pack(side=tk.RIGHT, padx=4, before=self._clearButton)

        self._statusMessage = tk.StringVar(value="")
        self._statusLabel = ttk.Label(self, textvariable=self._statusMessage, anchor=tk.W)
        self._statusLabel.pack(fill=tk.X, padx=4, pady=(4, 0))
        self._statusClearJobId: str | None = None

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
        self._tree.bind("<<TreeviewSelect>>", self._handleSelect)

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

    def refresh(self) -> None:
        selection = self._tree.selection()
        if selection:
            previousItemId = selection[0]
            previousEvent = self._itemEvents.get(previousItemId)
            if previousEvent:
                self._setSelectedEvent(previousEvent)

        rows = self._filteredRows()
        self._itemEvents.clear()

        for item in self._tree.get_children():
            self._tree.delete(item)

        matchedItemId = None
        for event in rows:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(event.ts))
            contextText = json.dumps(event.ctx, ensure_ascii=False)
            message = event.message or ""
            values = (timestamp, event.level, event.category, event.event, f"{message} {contextText}".strip())
            itemId = self._tree.insert("", "end", values=values)
            self._itemEvents[itemId] = event
            if matchedItemId is None and self._selectedEventMatches(event):
                matchedItemId = itemId
                self._setSelectedEvent(event)

        if matchedItemId is not None:
            self._tree.selection_set(matchedItemId)
            self._tree.see(matchedItemId)
        else:
            self._setSelectedEvent(None)

        self.after(1000, self.refresh)

    def _openDetails(self, _event: tk.Event[tk.Misc]) -> None:
        event = self._selectedEvent
        if event is None:
            selection = self._tree.selection()
            if not selection:
                return
            itemId = selection[0]
            event = self._itemEvents.get(itemId)
            if event:
                self._setSelectedEvent(event)
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

    def _copyLogToClipboard(self) -> None:
        rows = self._filteredRows()
        if not rows:
            self._setStatusMessage("No log entries to copy")
            return

        lines = []
        for event in rows:
            eventPayload = {
                "ts": event.ts,
                "level": event.level,
                "category": event.category,
                "event": event.event,
                "message": event.message,
                "ctx": event.ctx,
            }
            lines.append(json.dumps(eventPayload, ensure_ascii=False))

        fullText = "\n".join(lines)
        try:
            self.clipboard_clear()
            self.clipboard_append(fullText)
        except tk.TclError:
            self._setStatusMessage("Unable to access clipboard")
            return

        self._setStatusMessage(f"Copied {len(rows)} log entries")

    def _scrollToBottom(self) -> None:
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])

    def _handleSelect(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self._tree.selection()
        if not selection:
            self._setSelectedEvent(None)
            return
        itemId = selection[0]
        event = self._itemEvents.get(itemId)
        self._setSelectedEvent(event)

    def _setSelectedEvent(self, event: LogEvent | None) -> None:
        self._selectedEvent = event
        self._selectedEventKey = self._eventKey(event) if event else None

    def _selectedEventMatches(self, event: LogEvent) -> bool:
        if self._selectedEvent is event:
            return True
        if self._selectedEventKey is None:
            return False
        return self._eventKey(event) == self._selectedEventKey

    def _eventKey(self, event: LogEvent | None) -> tuple | None:
        if event is None:
            return None
        contextText = json.dumps(event.ctx, ensure_ascii=False, sort_keys=True)
        message = event.message or ""
        return (
            event.ts,
            event.level,
            event.category,
            event.event,
            message,
            contextText,
        )

    def _setStatusMessage(self, message: str, duration: int = 2000) -> None:
        if self._statusClearJobId is not None:
            self.after_cancel(self._statusClearJobId)
            self._statusClearJobId = None

        self._statusMessage.set(message)

        if message:
            self._statusClearJobId = self.after(duration, self._clearStatusMessage)

    def _clearStatusMessage(self) -> None:
        self._statusMessage.set("")
        self._statusClearJobId = None


__all__ = ["LogsPage"]

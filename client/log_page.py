"""Tkinter page for displaying structured client logs."""

from __future__ import annotations

import json
import time
import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Sequence

from .json_viewer import showJsonViewer
from .logbus import BUS, CATEGORIES, LogEvent


@dataclass(frozen=True)
class LogsPageConfig:
    """Configuration helper for tailoring the :class:`LogsPage` widget."""

    allowedCategories: Sequence[str] | None = None
    showCategoryFilters: bool = True
    defaultCategoryState: Dict[str, bool] = field(default_factory=dict)
    description: str | None = None
    placeholderEvents: Sequence[Dict[str, object]] = ()
    requireContent: bool = False
    levelFilter: Callable[[LogEvent], bool] | None = None
    searchLabel: str = "Search"


class LogsPage(ttk.Frame):
    def __init__(self, parent: tk.Misc, config: LogsPageConfig | None = None):
        super().__init__(parent)
        self._config = config or LogsPageConfig()
        self._categoryVariables: Dict[str, tk.BooleanVar] = {}
        self._itemEvents: Dict[str, LogEvent] = {}
        self._selectedEvent: LogEvent | None = None
        self._selectedEventKey: tuple | None = None
        self._allowedCategories = self._resolveAllowedCategories()
        self._placeholderEvents = list(self._config.placeholderEvents)

        self.toolbar = ttk.Frame(self)
        self.toolbar.pack(fill=tk.X)

        resolvedCategories = list(self._allowedCategories or CATEGORIES)
        if self._config.showCategoryFilters:
            for category in resolvedCategories:
                defaultValue = self._resolveDefaultCategoryState(category)
                variable = tk.BooleanVar(value=defaultValue)
                self._categoryVariables[category] = variable
                ttk.Checkbutton(
                    self.toolbar,
                    text=category,
                    variable=variable,
                    command=self.refresh,
                ).pack(side=tk.LEFT, padx=4)
        else:
            for category in resolvedCategories:
                defaultValue = self._resolveDefaultCategoryState(category)
                self._categoryVariables[category] = tk.BooleanVar(value=defaultValue)

        self._searchValue = tk.StringVar()
        ttk.Entry(self.toolbar, textvariable=self._searchValue, width=28).pack(side=tk.RIGHT, padx=6)
        ttk.Label(self.toolbar, text=self._config.searchLabel or "Search").pack(side=tk.RIGHT)

        self._scrollButton = ttk.Button(self.toolbar, text="Scroll to bottom", command=self._scrollToBottom)
        self._scrollButton.pack(side=tk.RIGHT, padx=4)

        self._clearButton = ttk.Button(self.toolbar, text="Clear", command=self._handleClear)
        self._clearButton.pack(side=tk.RIGHT, padx=4)

        self.copyAllLogsButton = ttk.Button(
            self.toolbar,
            text="Copy all logs",
            command=self.copyAllLogsToClipboard,
        )
        self.copyAllLogsButton.pack(side=tk.RIGHT, padx=4, before=self._clearButton)

        self.copySelectedLogButton = ttk.Button(
            self.toolbar,
            text="Copy selected log",
            command=self.copySelectedLogToClipboard,
        )
        self.copySelectedLogButton.pack(side=tk.RIGHT, padx=4, before=self.copyAllLogsButton)

        descriptionText = (self._config.description or "").strip()
        if descriptionText:
            self._descriptionLabel = ttk.Label(self, text=descriptionText, anchor=tk.W, wraplength=900)
            self._descriptionLabel.pack(fill=tk.X, padx=4, pady=(4, 0))
        else:
            self._descriptionLabel = None

        self._statusMessage = tk.StringVar(value="")
        self._statusLabel = ttk.Label(self, textvariable=self._statusMessage, anchor=tk.W)
        statusPadding = (0, 0) if descriptionText else (4, 0)
        self._statusLabel.pack(fill=tk.X, padx=4, pady=statusPadding)
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
        allowedCategories = set(self._allowedCategories or CATEGORIES)
        events = BUS.snapshot()[-2000:]
        rows: List[LogEvent] = []
        for event in events:
            if allowedCategories and event.category not in allowedCategories:
                continue
            if enabledCategories and event.category not in enabledCategories:
                continue
            if self._config.levelFilter and not self._config.levelFilter(event):
                continue
            if self._config.requireContent and not (event.message or event.ctx):
                continue
            if searchFilter:
                contextText = json.dumps(event.ctx, ensure_ascii=False)
                combined = f"{event.message} {contextText}".lower()
                if searchFilter not in combined:
                    continue
            rows.append(event)
        if not rows and self._placeholderEvents:
            placeholderRows = []
            baseTimestamp = time.time()
            for index, placeholder in enumerate(self._placeholderEvents):
                placeholderEvent = self._buildPlaceholderEvent(placeholder, baseTimestamp + index)
                if placeholderEvent is None:
                    continue
                if enabledCategories and placeholderEvent.category not in enabledCategories:
                    continue
                if searchFilter:
                    contextText = json.dumps(placeholderEvent.ctx, ensure_ascii=False)
                    combined = f"{placeholderEvent.message} {contextText}".lower()
                    if searchFilter not in combined:
                        continue
                placeholderRows.append(placeholderEvent)
            rows = placeholderRows
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

    def copyAllLogsToClipboard(self) -> None:
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
        if not self._copyTextToClipboard(fullText):
            return

        self._setStatusMessage(f"Copied {len(rows)} log entries")

    def copySelectedLogToClipboard(self) -> None:
        event = self._selectedEvent
        if event is None:
            self._setStatusMessage("No log entry selected")
            return

        eventPayload = {
            "ts": event.ts,
            "level": event.level,
            "category": event.category,
            "event": event.event,
            "message": event.message,
            "ctx": event.ctx,
        }
        fullText = json.dumps(eventPayload, ensure_ascii=False)
        if not self._copyTextToClipboard(fullText):
            return

        self._setStatusMessage("Copied selected log entry")

    def _copyTextToClipboard(self, text: str) -> bool:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            self._setStatusMessage("Unable to access clipboard")
            return False

        return True

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

    def _resolveAllowedCategories(self) -> Iterable[str]:
        if self._config.allowedCategories:
            categories: List[str] = []
            for category in self._config.allowedCategories:
                if category not in categories:
                    categories.append(category)
            return categories
        return list(CATEGORIES)

    def _resolveDefaultCategoryState(self, category: str) -> bool:
        if category in self._config.defaultCategoryState:
            return bool(self._config.defaultCategoryState[category])
        return category != "conn-error"

    def _buildPlaceholderEvent(
        self, placeholder: Dict[str, object], timestamp: float
    ) -> LogEvent | None:
        category = str(placeholder.get("category") or "").strip()
        if not category:
            return None
        level = str(placeholder.get("level") or "INFO").upper()
        eventName = str(placeholder.get("event") or "placeholder")
        message = str(placeholder.get("message") or "")
        contextValue = placeholder.get("ctx")
        if isinstance(contextValue, dict):
            context = contextValue
        else:
            context = {}
        return LogEvent(timestamp, level, category, eventName, message, context)


__all__ = ["LogsPage", "LogsPageConfig"]

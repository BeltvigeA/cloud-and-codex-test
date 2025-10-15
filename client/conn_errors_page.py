"""Tkinter page for printer connection error events."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from .json_viewer import showJsonViewer
from .logbus import BUS, LogEvent


class ConnErrorsPage(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self._itemEvents: Dict[str, LogEvent] = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="Printer connection errors (hidden by default)").pack(side=tk.LEFT, padx=6)

        clearButton = ttk.Button(toolbar, text="Clear", command=self._clearErrors)
        clearButton.pack(side=tk.RIGHT, padx=4)

        self._tree = ttk.Treeview(
            self,
            columns=("time", "serial", "ip", "event", "error"),
            show="headings",
            height=20,
        )
        for column, width in (
            ("time", 160),
            ("serial", 180),
            ("ip", 150),
            ("event", 200),
            ("error", 600),
        ):
            self._tree.heading(column, text=column)
            self._tree.column(column, width=width, anchor=tk.W)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.bind("<Double-1>", self._openDetails)

        self.after(500, self.refresh)

    def _rows(self) -> List[LogEvent]:
        return [event for event in BUS.snapshot() if event.category == "conn-error"][-2000:]

    def refresh(self) -> None:
        events = self._rows()
        self._itemEvents.clear()

        for item in self._tree.get_children():
            self._tree.delete(item)

        for event in events:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(event.ts))
            context = event.ctx or {}
            values = (
                timestamp,
                context.get("serial", ""),
                context.get("ip", ""),
                event.event,
                context.get("error") or context.get("detail") or event.message,
            )
            itemId = self._tree.insert("", "end", values=values)
            self._itemEvents[itemId] = event

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
            f"Conn Error · {event.event}",
            {
                "ts": event.ts,
                "level": event.level,
                "event": event.event,
                "ctx": event.ctx,
            },
        )

    def _clearErrors(self) -> None:
        BUS.clear(category="conn-error")
        self.refresh()


__all__ = ["ConnErrorsPage"]

"""Tkinter page dedicated to print job log events."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from .json_viewer import showJsonViewer
from .logbus import BUS, LogEvent


class PrintJobsPage(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self._itemEvents: Dict[str, LogEvent] = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X)

        clearButton = ttk.Button(toolbar, text="Clear Print Jobs", command=self._clearPrintJobs)
        clearButton.pack(side=tk.RIGHT, padx=4)

        scrollButton = ttk.Button(toolbar, text="Scroll to bottom", command=self._scrollToBottom)
        scrollButton.pack(side=tk.RIGHT, padx=4)

        self._tree = ttk.Treeview(
            self,
            columns=("time", "jobId", "file", "serial", "ip", "event", "progress"),
            show="headings",
            height=20,
        )
        for column, width in (
            ("time", 160),
            ("jobId", 180),
            ("file", 260),
            ("serial", 150),
            ("ip", 140),
            ("event", 160),
            ("progress", 100),
        ):
            self._tree.heading(column, text=column)
            self._tree.column(column, width=width, anchor=tk.W)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.bind("<Double-1>", self._openDetails)

        self.after(500, self.refresh)

    def _rows(self) -> List[LogEvent]:
        return [event for event in BUS.snapshot() if event.category == "print-job"][-2000:]

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
                context.get("jobId", ""),
                context.get("file")
                or context.get("remote")
                or context.get("filename")
                or context.get("remoteFile", ""),
                context.get("serial", ""),
                context.get("ip", ""),
                event.event,
                context.get("progress", ""),
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
            f"Print Job · {event.event}",
            {
                "ts": event.ts,
                "level": event.level,
                "event": event.event,
                "ctx": event.ctx,
            },
        )

    def _clearPrintJobs(self) -> None:
        BUS.clear(category="print-job")
        self.refresh()

    def _scrollToBottom(self) -> None:
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])


__all__ = ["PrintJobsPage"]

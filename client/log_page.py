"""Tkinter page for displaying structured client logs."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk
import time

from .logbus import BUS, LogEvent

_CATEGORIES = ['listener', 'control', 'status-base44', 'status-printer', 'error']


class LogsPage(ttk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self._categoryVariables = {category: tk.BooleanVar(value=True) for category in _CATEGORIES}
        filterFrame = ttk.Frame(self)
        filterFrame.pack(fill=tk.X)
        for category in _CATEGORIES:
            ttk.Checkbutton(
                filterFrame,
                text=category,
                variable=self._categoryVariables[category],
                command=self.refresh,
            ).pack(side=tk.LEFT, padx=6)

        self._search = tk.StringVar()
        ttk.Entry(filterFrame, textvariable=self._search).pack(side=tk.RIGHT, padx=6)
        ttk.Label(filterFrame, text='Søk').pack(side=tk.RIGHT)

        self._tree = ttk.Treeview(
            self,
            columns=('time', 'level', 'category', 'event', 'message'),
            show='headings',
            height=20,
        )
        for column, width in (
            ('time', 160),
            ('level', 70),
            ('category', 120),
            ('event', 160),
            ('message', 800),
        ):
            self._tree.heading(column, text=column)
            self._tree.column(column, width=width, anchor=tk.W)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self.after(500, self.refresh)

    def refresh(self) -> None:
        searchValue = self._search.get().strip().lower()
        activeCategories = {key for key, value in self._categoryVariables.items() if value.get()}
        for item in self._tree.get_children():
            self._tree.delete(item)

        events: list[LogEvent] = BUS.snapshot()[-1000:]
        for event in events:
            if event.category not in activeCategories:
                continue
            contextSerialized = json.dumps(event.ctx, ensure_ascii=False)
            combinedText = f"{event.message} {contextSerialized}".lower()
            if searchValue and searchValue not in combinedText:
                continue
            timestampText = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(event.ts))
            messageValue = f"{event.message} | {contextSerialized}".strip(" |")
            self._tree.insert(
                '',
                'end',
                values=(timestampText, event.level, event.category, event.event, messageValue),
            )

        self.after(1000, self.refresh)


__all__ = ['LogsPage']

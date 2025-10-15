"""Utility dialog for inspecting JSON-like payloads in a tree view."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict


def _insertDict(tree: ttk.Treeview, parent: str, value: Dict[str, Any]) -> None:
    for key, item in value.items():
        if isinstance(item, dict):
            nodeId = tree.insert(parent, "end", text=str(key), values=("",))
            _insertDict(tree, nodeId, item)
        elif isinstance(item, list):
            listNodeId = tree.insert(parent, "end", text=f"{key} []", values=(f"{len(item)} items",))
            for index, entry in enumerate(item):
                childText = f"[{index}]"
                if isinstance(entry, dict):
                    childId = tree.insert(listNodeId, "end", text=childText, values=("",))
                    _insertDict(tree, childId, entry)
                else:
                    tree.insert(listNodeId, "end", text=childText, values=(str(entry),))
        else:
            tree.insert(parent, "end", text=str(key), values=(str(item),))


def showJsonViewer(root: tk.Misc, title: str, payload: Dict[str, Any] | None) -> None:
    window = tk.Toplevel(root)
    window.title(title)
    window.geometry("800x520")

    tree = ttk.Treeview(window, columns=("value",), show="tree headings")
    tree.heading("#0", text="Field")
    tree.heading("value", text="Value")
    tree.pack(fill=tk.BOTH, expand=True)

    _insertDict(tree, "", payload or {})
    tree.focus_set()

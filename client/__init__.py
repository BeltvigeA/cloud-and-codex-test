"""Client utilities package providing CLI helpers, GUI, and offline tools."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = ["client", "gui", "commands", "base44Status", "pending"]

if TYPE_CHECKING:  # pragma: no cover - imported only for typing support
    from . import client as clientModule
    from . import gui as guiModule


def __getattr__(name: str) -> Any:
    """Lazily expose submodules to avoid circular import issues."""
    if name in __all__:
        lazyModule = import_module(f".{name}", __name__)
        globals()[name] = lazyModule
        return lazyModule
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Include lazily exposed attributes in dir(client)."""
    return sorted(set(globals()) | set(__all__))

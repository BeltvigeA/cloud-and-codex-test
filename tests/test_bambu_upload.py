"""Tests for Bambu printer FTPS uploads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import sys

from ftplib import error_perm


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from client import bambuPrinter


class DummyFtpClient:
    """Minimal stand-in for ``ImplicitFtpTls`` to capture FTPS interactions."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 - documented in class docstring
        self.args = args
        self.kwargs = kwargs
        self.connected: Tuple[str, int, int | None] | None = None
        self.timeout: int | None = None
        self.commands: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []
        self.cwdCalls: List[str] = []
        self.storageCommand: str | None = None
        self.storedData: bytes = b""
        self.closed = False
        self.failCwd = False
        self.deletedPaths: List[str] = []
        self.deleteFailures: Dict[str, Exception] = {}
        self.sendcmdCalls: List[str] = []
        self.storbinaryFailures: List[Exception] = []

    def connect(self, host: str, port: int, timeout: int | None = None, source_address=None):  # type: ignore[override]
        self.connected = (host, port, timeout)
        return "220 dummy"

    def login(self, user: str, password: str) -> None:
        self.commands.append(("login", (user, password), {}))

    def prot_p(self) -> None:
        self.commands.append(("prot_p", tuple(), {}))

    def set_pasv(self, enabled: bool) -> None:
        self.commands.append(("set_pasv", (enabled,), {}))

    def voidcmd(self, command: str) -> None:
        self.commands.append(("voidcmd", (command,), {}))

    def cwd(self, path: str) -> None:
        self.cwdCalls.append(path)
        if self.failCwd:
            raise RuntimeError("CWD failed")

    def storbinary(self, command: str, handle, blocksize: int = 8192) -> None:  # noqa: ANN001 - signature matches ftplib
        self.commands.append(("storbinary", (command,), {"blocksize": blocksize}))
        if self.storbinaryFailures:
            failure = self.storbinaryFailures.pop(0)
            raise failure
        self.storageCommand = command
        self.storedData = handle.read()

    def delete(self, path: str) -> None:
        self.deletedPaths.append(path)
        self.commands.append(("delete", (path,), {}))
        failure = self.deleteFailures.get(path)
        if failure:
            raise failure

    def sendcmd(self, command: str) -> str:
        self.sendcmdCalls.append(command)
        self.commands.append(("sendcmd", (command,), {}))
        if command in {"SITE FAIL"}:
            raise RuntimeError("SITE command failed")
        return "200 OK"

    def voidresp(self) -> None:
        self.commands.append(("voidresp", tuple(), {}))

    def quit(self) -> None:
        self.closed = True


@pytest.fixture
def temp_file(tmp_path: Path) -> Path:
    file_path = tmp_path / "test.3mf"
    file_path.write_bytes(b"dummy-data")
    return file_path


def _install_dummy(monkeypatch: pytest.MonkeyPatch, dummy: DummyFtpClient) -> None:
    def factory(*args: Any, **kwargs: Any) -> DummyFtpClient:
        return dummy

    monkeypatch.setattr(bambuPrinter, "ImplicitFtpTls", factory)  # type: ignore[assignment]


def test_upload_via_ftps_changes_directory(monkeypatch: pytest.MonkeyPatch, temp_file: Path) -> None:
    dummy = DummyFtpClient()
    _install_dummy(monkeypatch, dummy)

    result = bambuPrinter.uploadViaFtps(
        ip="192.0.2.10",
        accessCode="abcd",
        localPath=temp_file,
        remoteName="example.3mf",
    )

    assert result == "example.3mf"
    assert dummy.connected == ("192.0.2.10", 990, 120)
    assert dummy.cwdCalls and dummy.cwdCalls[0] in {"/sdcard", "sdcard"}
    assert dummy.storageCommand == "STOR example.3mf"
    assert dummy.storedData == b"dummy-data"
    assert dummy.deletedPaths == ["example.3mf"]
    assert dummy.closed is True
    storbinaryCalls = [entry for entry in dummy.commands if entry[0] == "storbinary"]
    assert storbinaryCalls, "Expected storbinary to be invoked"
    assert storbinaryCalls[0][2]["blocksize"] == 64 * 1024


def test_upload_via_ftps_falls_back_when_cwd_fails(monkeypatch: pytest.MonkeyPatch, temp_file: Path) -> None:
    dummy = DummyFtpClient()
    dummy.failCwd = True
    _install_dummy(monkeypatch, dummy)

    result = bambuPrinter.uploadViaFtps(
        ip="192.0.2.10",
        accessCode="abcd",
        localPath=temp_file,
        remoteName="example.3mf",
    )

    assert result == "example.3mf"
    assert dummy.storageCommand == "STOR sdcard/example.3mf"
    assert dummy.storedData == b"dummy-data"
    assert dummy.deletedPaths == ["sdcard/example.3mf"]


def test_upload_via_ftps_ignores_missing_file(monkeypatch: pytest.MonkeyPatch, temp_file: Path) -> None:
    dummy = DummyFtpClient()
    dummy.deleteFailures["example.3mf"] = RuntimeError("550 File not found")
    _install_dummy(monkeypatch, dummy)

    result = bambuPrinter.uploadViaFtps(
        ip="192.0.2.10",
        accessCode="abcd",
        localPath=temp_file,
        remoteName="example.3mf",
    )

    assert result == "example.3mf"
    assert dummy.deletedPaths == ["example.3mf"]
    storbinaryCalls = [entry for entry in dummy.commands if entry[0] == "storbinary"]
    assert storbinaryCalls, "Expected storbinary to be invoked even when delete reports missing file"


def test_upload_via_ftps_retries_after_reactivating_stor(monkeypatch: pytest.MonkeyPatch, temp_file: Path) -> None:
    dummy = DummyFtpClient()
    dummy.storbinaryFailures.append(error_perm("550 Permission denied"))
    _install_dummy(monkeypatch, dummy)
    monkeypatch.setenv("BAMBU_FTPS_REACTIVATE_STOR_COMMANDS", "RESET_STOR")

    result = bambuPrinter.uploadViaFtps(
        ip="192.0.2.10",
        accessCode="abcd",
        localPath=temp_file,
        remoteName="example.3mf",
    )

    assert result == "example.3mf"
    sendcmdCalls = [entry for entry in dummy.sendcmdCalls if entry.startswith("SITE ")]
    assert sendcmdCalls == ["SITE RESET_STOR"]
    storbinaryCalls = [entry for entry in dummy.commands if entry[0] == "storbinary"]
    assert len(storbinaryCalls) == 2


def test_build_printer_transfer_file_name_trims_prefixes() -> None:
    local_path = Path("/downloads/123e4567-e89b-12d3-a456-426614174000_123e4567-e89b-12d3-a456-426614174000_Drage.3mf")
    result = bambuPrinter.buildPrinterTransferFileName(local_path)
    assert result == "Drage.3mf"


def test_build_printer_transfer_file_name_preserves_regular_names() -> None:
    local_path = Path("/downloads/Cool Model.3mf")
    result = bambuPrinter.buildPrinterTransferFileName(local_path)
    assert result == "Cool_Model.3mf"


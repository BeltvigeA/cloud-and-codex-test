from pathlib import Path
from typing import List

import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import build_executable


def test_build_pyinstaller_command_includes_expected_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    command = build_executable.buildPyInstallerCommand(str(tmp_path))

    assert command[:3] == ["/usr/bin/python3", "-m", "PyInstaller"]
    assert "--distpath" in command
    distIndex = command.index("--distpath")
    assert Path(command[distIndex + 1]) == tmp_path
    assert command[-1].endswith("client.py")


def test_build_executable_invokes_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recordedCommands: List[List[str]] = []

    def fakeCheckCall(command: List[str]) -> None:
        recordedCommands.append(command)

    monkeypatch.setattr(build_executable.subprocess, "check_call", fakeCheckCall)

    executablePath = build_executable.buildExecutable(
        str(tmp_path),
        oneFile=False,
        additionalData=["data.json;data/data.json"],
        executableName="custom-client",
    )

    assert executablePath == tmp_path / "custom-client"
    assert recordedCommands
    assert recordedCommands[0][0:3] == [sys.executable, "-m", "PyInstaller"]
    assert "--onefile" not in recordedCommands[0]


def test_main_uses_argument_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    capturedArguments = {}

    def fakeBuild(outputDirectory: str, oneFile: bool, additionalData: List[str], executableName: str) -> Path:
        capturedArguments.update(
            {
                "outputDirectory": outputDirectory,
                "oneFile": oneFile,
                "additionalData": additionalData,
                "executableName": executableName,
            }
        )
        return Path(outputDirectory) / f"{executableName}.exe"

    monkeypatch.setattr(build_executable, "buildExecutable", fakeBuild)

    build_executable.main(
        [
            "--outputDirectory",
            str(tmp_path),
            "--executableName",
            "custom", 
            "--noOneFile",
            "--addData",
            "extras;extras",
        ]
    )

    out = capsys.readouterr().out.strip()
    assert out.endswith("custom.exe")
    assert capturedArguments == {
        "outputDirectory": str(tmp_path),
        "oneFile": False,
        "additionalData": ["extras;extras"],
        "executableName": "custom",
    }

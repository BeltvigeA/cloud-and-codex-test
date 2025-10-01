"""Utilities for building a standalone executable of the client CLI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence


def ensureOutputDirectory(outputDirectory: str) -> Path:
    outputPath = Path(outputDirectory).expanduser().resolve()
    outputPath.mkdir(parents=True, exist_ok=True)
    return outputPath


def buildPyInstallerCommand(
    outputDirectory: str,
    oneFile: bool = True,
    additionalData: Optional[List[str]] = None,
    executableName: str = "printer-client",
) -> List[str]:
    outputPath = ensureOutputDirectory(outputDirectory)
    buildPath = outputPath / "build"
    specPath = outputPath / "spec"
    buildPath.mkdir(parents=True, exist_ok=True)
    specPath.mkdir(parents=True, exist_ok=True)

    entryScript = Path(__file__).resolve().parent / "client.py"

    command: List[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        executableName,
        "--distpath",
        str(outputPath),
        "--workpath",
        str(buildPath),
        "--specpath",
        str(specPath),
    ]

    if oneFile:
        command.append("--onefile")

    for dataItem in additionalData or []:
        command.extend(["--add-data", dataItem])

    command.append(str(entryScript))
    return command


def buildExecutable(
    outputDirectory: str,
    oneFile: bool = True,
    additionalData: Optional[List[str]] = None,
    executableName: str = "printer-client",
) -> Path:
    command = buildPyInstallerCommand(
        outputDirectory,
        oneFile=oneFile,
        additionalData=additionalData,
        executableName=executableName,
    )
    subprocess.check_call(command)

    suffix = ".exe" if os.name == "nt" else ""
    outputPath = ensureOutputDirectory(outputDirectory)
    return outputPath / f"{executableName}{suffix}"


def parseArguments(argumentList: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a standalone executable of the printer client.",
    )
    parser.add_argument(
        "--outputDirectory",
        dest="outputDirectory",
        default="dist",
        help="Directory where the executable and build artifacts will be written.",
    )
    parser.add_argument(
        "--executableName",
        dest="executableName",
        default="printer-client",
        help="Base name of the generated executable.",
    )
    parser.add_argument(
        "--addData",
        dest="additionalData",
        action="append",
        default=[],
        help="Extra PyInstaller --add-data entries (format: SRC;DEST).",
    )
    parser.add_argument(
        "--noOneFile",
        dest="oneFile",
        action="store_false",
        help="Disable PyInstaller one-file bundling.",
    )
    parser.set_defaults(oneFile=True)

    return parser.parse_args(argumentList)


def main(argumentList: Optional[Sequence[str]] = None) -> None:
    arguments = parseArguments(argumentList)
    executablePath = buildExecutable(
        arguments.outputDirectory,
        oneFile=arguments.oneFile,
        additionalData=arguments.additionalData,
        executableName=arguments.executableName,
    )
    print(executablePath)


__all__ = [
    "buildExecutable",
    "buildPyInstallerCommand",
    "ensureOutputDirectory",
    "main",
    "parseArguments",
]


if __name__ == "__main__":
    main()

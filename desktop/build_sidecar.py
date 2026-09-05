"""Create a self-contained backend executable for the current Tauri target."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARIES = ROOT / "frontend" / "src-tauri" / "binaries"
COLLECT_ALL = ("libsql", "asyncpg", "tzdata")


def target_suffix(system: str | None = None, machine: str | None = None) -> str:
    """Return Tauri's documented externalBin target-triple suffix."""
    system = (system or platform.system()).lower()
    machine = {
        "arm64": "aarch64",
        "aarch64": "aarch64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }.get((machine or platform.machine()).lower(), (machine or platform.machine()).lower())
    os_name = {"darwin": "apple-darwin", "windows": "pc-windows-msvc", "linux": "unknown-linux-gnu"}.get(system)
    if not os_name:
        raise RuntimeError(f"Unsupported desktop platform: {platform.system()}")
    return f"{machine}-{os_name}"


def sidecar_filename(system: str | None = None, machine: str | None = None) -> str:
    """Name a sidecar exactly as Tauri's externalBin resolver expects."""
    normalized_system = (system or platform.system()).lower()
    extension = ".exe" if normalized_system == "windows" else ""
    return f"mindcore-backend-{target_suffix(normalized_system, machine)}{extension}"


def data_separator(system: str | None = None) -> str:
    """PyInstaller uses ';' on Windows and ':' on POSIX for --add-data."""
    return ";" if (system or platform.system()).lower() == "windows" else ":"


def main() -> None:
    BINARIES.mkdir(parents=True, exist_ok=True)
    dist = ROOT / "build" / "sidecar"
    separator = data_separator()
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--distpath", str(dist),
        "--name", "mindcore-backend",
        # Public bundle: include only generic runtime prompts. The private
        # A developer persona identity intentionally stays outside the artifact.
        "--add-data", f"{ROOT / 'app' / 'prompts' / 'identity_template.txt'}{separator}app/prompts",
        "--add-data", f"{ROOT / 'app' / 'prompts' / 'memory_extraction.txt'}{separator}app/prompts",
        "--add-data", f"{ROOT / 'app' / 'prompts' / 'skills'}{separator}app/prompts/skills",
        "--add-data", f"{ROOT / 'db' / 'turso' / 'baseline_v1.sql'}{separator}db/turso",
        *[item for package in COLLECT_ALL for item in ("--collect-all", package)],
        str(ROOT / "app" / "desktop_backend.py"),
    ], cwd=ROOT, check=True)
    extension = ".exe" if platform.system().lower() == "windows" else ""
    source = dist / f"mindcore-backend{extension}"
    destination = BINARIES / sidecar_filename()
    shutil.copy2(source, destination)
    print(destination)


if __name__ == "__main__":
    main()

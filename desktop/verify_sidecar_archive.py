"""Validate the dependency boundary of a built PyInstaller desktop sidecar."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path


FORBIDDEN_WEBSOCKET_SPEEDUPS = "websockets/speedups"
REQUIRED_MEMBER_PREFIXES = ("libsql/", "asyncpg/")


def validate_archive_members(members: Iterable[str]) -> None:
    """Reject optional WebSocket speedups while retaining required DB packages."""
    normalized = tuple(member.replace("\\", "/").lower() for member in members)
    forbidden = [member for member in normalized if FORBIDDEN_WEBSOCKET_SPEEDUPS in member]
    if forbidden:
        raise RuntimeError(
            "The desktop sidecar contains the unneeded WebSocket speedups module: "
            + ", ".join(forbidden)
        )
    missing = [
        prefix for prefix in REQUIRED_MEMBER_PREFIXES
        if not any(member.startswith(prefix) for member in normalized)
    ]
    if missing:
        raise RuntimeError("The desktop sidecar is missing required database packages: " + ", ".join(missing))


def archive_members(sidecar: Path) -> tuple[str, ...]:
    """Read PyInstaller's final CArchive without extracting or altering it."""
    from PyInstaller.archive.readers import CArchiveReader

    return tuple(CArchiveReader(sidecar).toc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a MindCore PyInstaller sidecar")
    parser.add_argument("sidecar", type=Path)
    sidecar = parser.parse_args().sidecar
    if not sidecar.is_file():
        raise SystemExit(f"Sidecar does not exist: {sidecar}")
    members = archive_members(sidecar)
    validate_archive_members(members)
    print("SIDECAR_ARCHIVE_DEPENDENCIES_OK")


if __name__ == "__main__":
    main()

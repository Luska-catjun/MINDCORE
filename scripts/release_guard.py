"""Deterministic release metadata and one-time schedule guards."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("release timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _cargo_lock_version() -> str:
    text = (ROOT / "frontend/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    match = re.search(
        r'\[\[package\]\]\s+name = "mindcore"\s+version = "([^"]+)"',
        text,
    )
    if not match:
        raise ValueError("MindCore package is missing from Cargo.lock")
    return match.group(1)


def release_versions() -> dict[str, str]:
    cargo = tomllib.loads(
        (ROOT / "frontend/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    )
    tauri = json.loads(
        (ROOT / "frontend/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    frontend = json.loads(
        (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    )
    frontend_lock = json.loads(
        (ROOT / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    app_main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    api_match = re.search(r"FastAPI\([^\n]+version=\"([^\"]+)\"", app_main)
    if not api_match:
        raise ValueError("FastAPI version metadata is missing")
    return {
        "cargo": str(cargo["package"]["version"]),
        "cargo_lock": _cargo_lock_version(),
        "tauri": str(tauri["version"]),
        "frontend": str(frontend["version"]),
        "frontend_lock": str(frontend_lock["version"]),
        "frontend_lock_root": str(frontend_lock["packages"][""]["version"]),
        "api": api_match.group(1),
    }


def require_version(expected: str) -> None:
    mismatches = {
        source: version
        for source, version in release_versions().items()
        if version != expected
    }
    if mismatches:
        details = ", ".join(f"{source}={version}" for source, version in mismatches.items())
        raise SystemExit(f"Release version metadata mismatch: expected {expected}; {details}")


def schedule_eligible(*, now: str, starts_at: str, expires_at: str, version: str) -> bool:
    current = _parse_utc(now)
    start = _parse_utc(starts_at)
    expiry = _parse_utc(expires_at)
    if not start < expiry:
        raise ValueError("release eligibility window must be positive")
    if not start <= current < expiry:
        return False
    require_version(version)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    version_parser = subparsers.add_parser("version")
    version_parser.add_argument("--expected", required=True)
    schedule_parser = subparsers.add_parser("schedule")
    schedule_parser.add_argument("--now", required=True)
    schedule_parser.add_argument("--starts-at", required=True)
    schedule_parser.add_argument("--expires-at", required=True)
    schedule_parser.add_argument("--version", required=True)
    args = parser.parse_args()

    if args.command == "version":
        require_version(args.expected)
        print("VERSION_METADATA_OK")
        return
    eligible = schedule_eligible(
        now=args.now,
        starts_at=args.starts_at,
        expires_at=args.expires_at,
        version=args.version,
    )
    print(f"eligible={'true' if eligible else 'false'}")


if __name__ == "__main__":
    main()

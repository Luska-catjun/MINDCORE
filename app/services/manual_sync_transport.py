"""Dumb, path-safe file transport for pairing and authenticated envelopes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any

from app.services.persona_sync import canonical_json
from app.services.persona_sync_crypto import (MAX_ENVELOPE_BYTES,
                                               SyncSecurityError)


PAIRING_EXTENSION = ".mindcorepair"
SYNC_EXTENSION = ".mindcoresync"
PAIRING_LIMIT = 8_192
SYNC_LIMIT = MAX_ENVELOPE_BYTES
PAIRING_FIELDS = {"artifact_type", "device_id", "fingerprint", "protocol_version", "public_key"}
ENVELOPE_FIELDS = {"ciphertext", "message_id", "nonce", "persona_id", "protocol_version",
                   "recipient_device_id", "sender_device_id", "sequence", "type"}


def _validated_destination(destination: str | Path, extension: str) -> Path:
    raw = os.fspath(destination)
    if "\x00" in raw:
        raise SyncSecurityError("ARTIFACT_PATH_INVALID")
    path = Path(raw)
    if ".." in path.parts or path.suffix != extension:
        raise SyncSecurityError("ARTIFACT_PATH_INVALID")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise SyncSecurityError("ARTIFACT_PATH_INVALID")
    resolved = parent / path.name
    try:
        mode = resolved.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise SyncSecurityError("ARTIFACT_PATH_INVALID")
        raise SyncSecurityError("ARTIFACT_ALREADY_EXISTS")
    except FileNotFoundError:
        return resolved


def _safe_read(source: str | Path, extension: str, size_limit: int) -> bytes:
    raw = os.fspath(source)
    if "\x00" in raw:
        raise SyncSecurityError("ARTIFACT_PATH_INVALID")
    path = Path(raw)
    if ".." in path.parts or path.suffix != extension:
        raise SyncSecurityError("ARTIFACT_PATH_INVALID")
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) \
                or info.st_size < 2 or info.st_size > size_limit:
            raise SyncSecurityError("ARTIFACT_FILE_INVALID")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            chunks = bytearray()
            while len(chunks) <= size_limit:
                part = os.read(descriptor, min(65_536, size_limit + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
        finally:
            os.close(descriptor)
    except SyncSecurityError:
        raise
    except OSError as error:
        raise SyncSecurityError("ARTIFACT_FILE_INVALID") from error
    if not 2 <= len(chunks) <= size_limit:
        raise SyncSecurityError("ARTIFACT_FILE_INVALID")
    return bytes(chunks)


def _write(destination: str | Path, extension: str, value: dict[str, Any], limit: int) -> Path:
    target = _validated_destination(destination, extension)
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) > limit:
        raise SyncSecurityError("ARTIFACT_SIZE_LIMIT")
    descriptor = None
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_NOFOLLOW", 0), 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    except OSError as error:
        if descriptor is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise SyncSecurityError("ARTIFACT_WRITE_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        parent_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except OSError:
        # Directory fsync is unsupported by some desktop filesystems; file data
        # was still fsynced and the destination was exclusively created.
        pass
    return target


def write_pairing_artifact(destination: str | Path, artifact: dict[str, Any]) -> Path:
    if not isinstance(artifact, dict) or set(artifact) != PAIRING_FIELDS \
            or artifact.get("artifact_type") != "mindcore_pairing":
        raise SyncSecurityError("PAIRING_ARTIFACT_INVALID")
    return _write(destination, PAIRING_EXTENSION, artifact, PAIRING_LIMIT)


def read_pairing_artifact(source: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(_safe_read(source, PAIRING_EXTENSION, PAIRING_LIMIT))
    except SyncSecurityError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SyncSecurityError("PAIRING_ARTIFACT_INVALID") from error
    if not isinstance(value, dict) or set(value) != PAIRING_FIELDS \
            or value.get("artifact_type") != "mindcore_pairing":
        raise SyncSecurityError("PAIRING_ARTIFACT_INVALID")
    return value


def write_sync_artifact(destination: str | Path, envelope: dict[str, Any]) -> Path:
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS \
            or envelope.get("type") != "persona_delta":
        raise SyncSecurityError("ENVELOPE_FIELDS_UNSUPPORTED")
    return _write(destination, SYNC_EXTENSION, envelope, SYNC_LIMIT)


def read_sync_artifact(source: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(_safe_read(source, SYNC_EXTENSION, SYNC_LIMIT))
    except SyncSecurityError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SyncSecurityError("ENVELOPE_MALFORMED") from error
    if not isinstance(value, dict) or set(value) != ENVELOPE_FIELDS \
            or value.get("type") != "persona_delta":
        raise SyncSecurityError("ENVELOPE_FIELDS_UNSUPPORTED")
    return value

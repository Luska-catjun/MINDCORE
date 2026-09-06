"""App-scoped, versioned ownership for foreground cognitive snapshots."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable


Snapshot = tuple[dict[str, Any], ...]


class VersionedSnapshot:
    """Keep one immutable snapshot and reject work older than a correction.

    The lock protects only in-memory tuple replacement. Database and other
    awaited work always happens outside it.
    """

    def __init__(self) -> None:
        self._rows: Snapshot = ()
        self._correction_epoch = 0
        self._lock = Lock()

    def read(self) -> Snapshot:
        with self._lock:
            return self._rows

    def begin_refresh(self) -> int:
        with self._lock:
            return self._correction_epoch

    def publish_replace(self, expected_epoch: int, rows: Snapshot) -> bool:
        with self._lock:
            if self._correction_epoch != expected_epoch:
                return False
            self._rows = rows
            return True

    def publish_update(
        self,
        expected_epoch: int,
        updater: Callable[[Snapshot], Snapshot],
    ) -> bool:
        with self._lock:
            if self._correction_epoch != expected_epoch:
                return False
            self._rows = updater(self._rows)
            return True

    def apply_correction(self, updater: Callable[[Snapshot], Snapshot]) -> Snapshot:
        with self._lock:
            self._rows = updater(self._rows)
            self._correction_epoch += 1
            return self._rows


@dataclass
class CognitiveSnapshotScope:
    narrative: VersionedSnapshot = field(default_factory=VersionedSnapshot)
    self_model: VersionedSnapshot = field(default_factory=VersionedSnapshot)


# Direct service callers retain a process-local compatibility scope. Every
# FastAPI app creates and passes its own scope, so app/database instances never
# share this fallback in production runtime paths.
DEFAULT_COGNITIVE_SNAPSHOT_SCOPE = CognitiveSnapshotScope()


def resolve_snapshot_scope(scope: CognitiveSnapshotScope | None) -> CognitiveSnapshotScope:
    return scope if scope is not None else DEFAULT_COGNITIVE_SNAPSHOT_SCOPE

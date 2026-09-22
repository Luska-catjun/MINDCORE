from __future__ import annotations

from contextlib import redirect_stderr
from contextlib import asynccontextmanager
from io import StringIO
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from app.services.startup_diagnostics import (
    DIAGNOSTIC_BUILD_LABEL,
    emit_startup_diagnostic,
    emit_startup_progress,
)


class StartupDiagnosticTests(unittest.TestCase):
    def test_progress_uses_fixed_vocabulary_and_no_runtime_values(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            emit_startup_progress(
                phase="migration_ledger",
                operation="ledger_commit",
                schema_version=21,
            )
        self.assertEqual(
            stderr.getvalue().strip(),
            "MINDCORE_STARTUP_PROGRESS "
            f"build={DIAGNOSTIC_BUILD_LABEL} "
            "phase=migration_ledger operation=ledger_commit schema_version=21",
        )

    def test_progress_rejects_values_outside_the_allowlist(self) -> None:
        with self.assertRaises(ValueError):
            emit_startup_progress(phase="bad/path", operation="ledger_commit")
        with self.assertRaises(ValueError):
            emit_startup_progress(phase="ready", operation="token=secret")

    def test_emits_only_fixed_secret_safe_fields(self) -> None:
        error = RuntimeError(
            "database libsql://private.example?authToken=secret at /Users/private/data.db"
        )
        stderr = StringIO()
        with redirect_stderr(stderr):
            emit_startup_diagnostic(
                phase="migration_ledger",
                category="driver",
                operation="ledger_commit",
                schema_version=21,
                error=error,
            )
        line = stderr.getvalue().strip()
        self.assertEqual(
            line,
            "MINDCORE_STARTUP_DIAGNOSTIC "
            f"build={DIAGNOSTIC_BUILD_LABEL} "
            "phase=migration_ledger category=driver operation=ledger_commit "
            "schema_version=21 exception_class=RuntimeError",
        )
        for forbidden in ("libsql", "secret", "/Users", "private.example", "data.db"):
            self.assertNotIn(forbidden, line)

    def test_invalid_identifiers_are_replaced_not_echoed(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            emit_startup_diagnostic(
                phase="bad/path",
                category="bad value",
                operation="token=secret",
                error=ValueError("sensitive input"),
            )
        self.assertEqual(
            stderr.getvalue().strip(),
            "MINDCORE_STARTUP_DIAGNOSTIC "
            f"build={DIAGNOSTIC_BUILD_LABEL} "
            "phase=startup category=runtime operation=unknown exception_class=ValueError",
        )

    def test_same_exception_is_not_reported_twice(self) -> None:
        error = RuntimeError("sensitive")
        stderr = StringIO()
        with redirect_stderr(stderr):
            emit_startup_diagnostic(phase="settings", category="configuration", error=error)
            emit_startup_diagnostic(phase="ready", category="runtime", error=error)
        self.assertEqual(stderr.getvalue().count("MINDCORE_STARTUP_DIAGNOSTIC"), 1)

    def test_database_pool_failure_is_classified_without_error_message(self) -> None:
        settings = Settings(_env_file=None, database_backend="supabase")
        app = create_app(
            settings_override=settings,
            db_pool_factory=lambda _settings: (_ for _ in ()).throw(
                ConnectionError("libsql://private.example token=secret")
            ),
        )
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(ConnectionError):
            with TestClient(app):
                pass
        line = stderr.getvalue()
        self.assertIn("phase=database_connect", line)
        self.assertIn("category=connection", line)
        self.assertNotIn("private.example", line)
        self.assertNotIn("secret", line)

    def test_narrative_and_self_model_hydration_have_distinct_phases(self) -> None:
        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield object()

            async def close(self):
                return None

        settings = Settings(_env_file=None, database_backend="supabase")
        for target, expected_phase in (
            ("app.main.hydrate_narrative_snapshot", "narrative_hydration"),
            ("app.main.hydrate_self_model_snapshot", "self_model_hydration"),
        ):
            app = create_app(settings_override=settings, db_pool_factory=lambda _settings: Pool())
            stderr = StringIO()
            patches = [
                patch("app.main.hydrate_narrative_snapshot", new=AsyncMock(return_value=None)),
                patch("app.main.hydrate_self_model_snapshot", new=AsyncMock(return_value=None)),
            ]
            selected = 0 if "narrative" in target else 1
            patches[selected] = patch(
                target,
                new=AsyncMock(side_effect=RuntimeError("/Users/private identity prompt secret")),
            )
            with patches[0], patches[1], redirect_stderr(stderr), self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass
            line = stderr.getvalue()
            self.assertIn(f"phase={expected_phase}", line)
            self.assertNotIn("/Users", line)
            self.assertNotIn("prompt", line)

    def test_normal_startup_progress_sequence_is_logical(self) -> None:
        class Pool:
            @asynccontextmanager
            async def acquire(self):
                yield object()

        settings = Settings(_env_file=None, database_backend="supabase")
        stderr = StringIO()
        with (
            patch("app.main.hydrate_narrative_snapshot", new=AsyncMock(return_value=None)),
            patch("app.main.hydrate_self_model_snapshot", new=AsyncMock(return_value=None)),
            redirect_stderr(stderr),
        ):
            app = create_app(
                settings_override=settings,
                db_pool_factory=lambda _settings: Pool(),
            )
            with TestClient(app):
                pass
        phases = [
            item.split("phase=", 1)[1].split(" ", 1)[0]
            for item in stderr.getvalue().splitlines()
            if item.startswith("MINDCORE_STARTUP_PROGRESS ")
        ]
        expected = [
            "settings",
            "identity",
            "database_connect",
            "narrative_hydration",
            "self_model_hydration",
            "ready",
        ]
        cursor = 0
        for phase in phases:
            if cursor < len(expected) and phase == expected[cursor]:
                cursor += 1
        self.assertEqual(cursor, len(expected), phases)


if __name__ == "__main__":
    unittest.main()

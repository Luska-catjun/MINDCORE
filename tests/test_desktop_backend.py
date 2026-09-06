from __future__ import annotations

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.desktop_backend import (
    DESKTOP_SHUTDOWN_CAPABILITY_HEADER,
    _desktop_session_token,
    _ensure_desktop_auth_config,
    _parent_process_is_alive,
    _register_desktop_shutdown_route,
)
from app.main import create_app
from app.routers.auth import is_valid_session_token


class DesktopBackendTests(TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_desktop_auth_provisioning_preserves_existing_config_and_issues_session(self) -> None:
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_URL=libsql://example.turso.io\nDATABASE_AUTH_TOKEN=test-token\n", encoding="utf-8")

            _ensure_desktop_auth_config(str(config))
            first_text = config.read_text(encoding="utf-8")
            _ensure_desktop_auth_config(str(config))

            self.assertEqual(config.read_text(encoding="utf-8"), first_text)
            self.assertIn("DATABASE_URL=libsql://example.turso.io", first_text)
            self.assertIn("PRIVATE_ACCESS_PASSWORD=", first_text)
            self.assertIn("AUTH_SIGNING_SECRET=", first_text)
            session = _desktop_session_token(str(config))
            self.assertTrue(is_valid_session_token(get_settings(), session))

    def test_parent_liveness_recognizes_the_current_process(self) -> None:
        self.assertTrue(_parent_process_is_alive(os.getpid()))

    def _shutdown_client(self):
        settings = Settings(
            _env_file=None,
            private_access_password="test-private-access-password",
            auth_signing_secret="test-auth-signing-secret",
        )
        app = create_app(settings_override=settings, db_pool_factory=lambda _settings: object())
        server = type("TestServer", (), {"should_exit": False})()
        capability = "desktop-shutdown-test-capability"
        _register_desktop_shutdown_route(app, server, capability)
        return TestClient(app), server, capability

    def test_desktop_shutdown_rejects_missing_capability(self) -> None:
        client, server, _capability = self._shutdown_client()
        with client:
            response = client.post("/_desktop/shutdown", headers={"Origin": "https://untrusted.example"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(server.should_exit)

    def test_desktop_shutdown_rejects_wrong_capability(self) -> None:
        client, server, _capability = self._shutdown_client()
        with client:
            response = client.post(
                "/_desktop/shutdown",
                headers={DESKTOP_SHUTDOWN_CAPABILITY_HEADER: "wrong-desktop-shutdown-capability"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(server.should_exit)

    def test_desktop_shutdown_accepts_parent_capability_without_url_or_auth_log_exposure(self) -> None:
        client, server, capability = self._shutdown_client()
        with client, self.assertNoLogs("diana.auth", level="INFO"):
            response = client.post(
                "/_desktop/shutdown",
                headers={DESKTOP_SHUTDOWN_CAPABILITY_HEADER: capability},
            )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(server.should_exit)
        self.assertNotIn(capability, str(response.request.url))
        self.assertNotIn(capability, response.text)

    @staticmethod
    def _mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_desktop_auth_provisioning_keeps_owner_only_permissions_with_umask_022(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode bits do not represent Windows ACLs")
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_URL=libsql://example.turso.io\n", encoding="utf-8")
            os.chmod(config, 0o600)
            previous_umask = os.umask(0o022)
            try:
                _ensure_desktop_auth_config(str(config))
            finally:
                os.umask(previous_umask)

            self.assertEqual(self._mode(config), 0o600)
            self.assertEqual(list(Path(directory).glob(".mindcore.env.*.tmp")), [])

    def test_desktop_auth_provisioning_restricts_a_group_readable_existing_config(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode bits do not represent Windows ACLs")
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            config.write_text("DATABASE_URL=libsql://example.turso.io\n", encoding="utf-8")
            os.chmod(config, 0o644)

            _ensure_desktop_auth_config(str(config))

            self.assertEqual(self._mode(config), 0o600)
            self.assertIn("PRIVATE_ACCESS_PASSWORD=", config.read_text(encoding="utf-8"))
            self.assertIn("AUTH_SIGNING_SECRET=", config.read_text(encoding="utf-8"))

    def test_desktop_auth_provisioning_preserves_existing_secret_content_and_mode(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode bits do not represent Windows ACLs")
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            original = (
                "DATABASE_URL=libsql://example.turso.io\n"
                "PRIVATE_ACCESS_PASSWORD=existing-private-secret\n"
                "AUTH_SIGNING_SECRET=existing-signing-secret\n"
            )
            config.write_text(original, encoding="utf-8")
            os.chmod(config, 0o600)

            _ensure_desktop_auth_config(str(config))

            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertEqual(self._mode(config), 0o600)

    def test_desktop_auth_provisioning_restricts_an_existing_secret_config(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode bits do not represent Windows ACLs")
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            original = (
                "DATABASE_URL=libsql://example.turso.io\n"
                "PRIVATE_ACCESS_PASSWORD=existing-private-secret\n"
                "AUTH_SIGNING_SECRET=existing-signing-secret\n"
            )
            config.write_text(original, encoding="utf-8")
            os.chmod(config, 0o644)

            _ensure_desktop_auth_config(str(config))

            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertEqual(self._mode(config), 0o600)

    def test_desktop_auth_provisioning_leaves_original_config_intact_when_replace_fails(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode bits do not represent Windows ACLs")
        with TemporaryDirectory() as directory:
            config = Path(directory) / "mindcore.env"
            original = "DATABASE_URL=libsql://example.turso.io\n"
            config.write_text(original, encoding="utf-8")
            os.chmod(config, 0o600)

            temporary_modes: list[int] = []

            def fail_replace(source, _destination) -> None:
                temporary_modes.append(self._mode(Path(source)))
                raise OSError("replace failed")

            with patch("app.desktop_backend.os.replace", side_effect=fail_replace):
                with self.assertRaises(OSError):
                    _ensure_desktop_auth_config(str(config))

            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertEqual(self._mode(config), 0o600)
            self.assertEqual(temporary_modes, [0o600])
            self.assertEqual(list(Path(directory).glob(".mindcore.env.*.tmp")), [])

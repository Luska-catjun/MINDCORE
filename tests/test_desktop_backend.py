from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.config import get_settings
from app.desktop_backend import (
    _desktop_session_token,
    _ensure_desktop_auth_config,
    _parent_process_is_alive,
)
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

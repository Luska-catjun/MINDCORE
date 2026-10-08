"""WRA native-reference boundary: metadata cannot replace a DB token."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from app.config import Settings


class WraSetupSettingsTests(unittest.TestCase):
    def test_native_reference_is_accepted_but_is_not_a_token_or_exported_secret(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = Path(directory) / "setup.env"
            config.write_text("DATABASE_CREDENTIAL_ID=wra-synthetic-id\n", encoding="utf-8")
            settings = Settings(_env_file=config)
            self.assertEqual(settings.database_credential_id, "wra-synthetic-id")
            self.assertIsNone(settings.database_auth_token)
            self.assertNotIn("database_credential_id", settings.model_dump())
            self.assertNotIn("wra-synthetic-id", repr(settings))

    def test_unrelated_unknown_configuration_still_fails_closed(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = Path(directory) / "setup.env"
            config.write_text("UNSUPPORTED_WRA_SETTING=synthetic\n", encoding="utf-8")
            with self.assertRaises(ValidationError):
                Settings(_env_file=config)

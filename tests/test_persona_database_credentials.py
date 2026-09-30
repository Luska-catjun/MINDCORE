from __future__ import annotations

import unittest

from app.services.persona_database_credentials import PersonaDatabaseCredentials
from app.services.persona_sync_crypto import SyncSecurityError


class MemoryKeyring:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get_secret(self, account: str) -> str | None:
        return self.values.get(account)

    def set_secret(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete_secret(self, account: str) -> None:
        self.values.pop(account, None)


class PersonaDatabaseCredentialTests(unittest.TestCase):
    def test_store_replace_lookup_delete_and_missing_are_fail_closed(self):
        backend = MemoryKeyring()
        credentials = PersonaDatabaseCredentials(backend)
        persona_id = "12345678-1234-4234-8234-123456789abc"

        credentials.store(persona_id, "synthetic-token-one")
        self.assertEqual(credentials.get(persona_id), "synthetic-token-one")
        credentials.store(persona_id, "synthetic-token-two")
        self.assertEqual(credentials.get(persona_id), "synthetic-token-two")
        credentials.delete(persona_id)
        with self.assertRaisesRegex(SyncSecurityError, "DATABASE_CREDENTIAL_MISSING"):
            credentials.get(persona_id)
        credentials.delete(persona_id)


    def test_credential_id_rejects_path_like_and_unbounded_values(self):
        for credential_id in ("../secret", "", "setup/../x", "x" * 37):
            with self.subTest(credential_id=credential_id):
                with self.assertRaisesRegex(SyncSecurityError, "DATABASE_CREDENTIAL_ID_INVALID"):
                    PersonaDatabaseCredentials(MemoryKeyring()).get(credential_id)


if __name__ == "__main__":
    unittest.main()

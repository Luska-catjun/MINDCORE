"""OS keyring storage for remote Persona database credentials.

Credential values are deliberately handled only in memory. The persistent
configuration contains an opaque Persona identifier that maps to this keyring
account; there is no file-backed fallback.
"""
from __future__ import annotations

from typing import Protocol

from app.services.persona_sync_crypto import NativeKeyringBackend, SyncSecurityError


SERVICE_NAME = "MindCore Persona Database"


class CredentialBackend(Protocol):
    def get_secret(self, account: str) -> str | None: ...
    def set_secret(self, account: str, value: str) -> None: ...
    def delete_secret(self, account: str) -> None: ...


class PersonaDatabaseCredentials:
    def __init__(self, backend: CredentialBackend | None = None):
        self._backend = backend

    def _store(self) -> CredentialBackend:
        return self._backend if self._backend is not None else NativeKeyringBackend(SERVICE_NAME)

    @staticmethod
    def _account(credential_id: str) -> str:
        if not isinstance(credential_id, str) or not (
            credential_id == "setup" or
            (len(credential_id) == 36 and all(c in "0123456789abcdef-" for c in credential_id))
        ):
            raise SyncSecurityError("DATABASE_CREDENTIAL_ID_INVALID")
        return credential_id

    def store(self, credential_id: str, value: str) -> None:
        account = self._account(credential_id)
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise SyncSecurityError("DATABASE_CREDENTIAL_INVALID")
        self._store().set_secret(account, value)

    def get(self, credential_id: str) -> str:
        value = self._store().get_secret(self._account(credential_id))
        if not value:
            raise SyncSecurityError("DATABASE_CREDENTIAL_MISSING")
        return value

    def delete(self, credential_id: str) -> None:
        self._store().delete_secret(self._account(credential_id))

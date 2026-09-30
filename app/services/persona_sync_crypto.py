"""Desktop implementation of Android MindCore secure envelope protocol v1.

Long-term P-256 private identity material is stored only in an OS credential
backend. Peer trust, identity binding and replay state live in a device-local
SQLite sidecar; no provider credential or Persona record is written there.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import sqlite3
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric import ec

from app.services.persona_sync import PROTOCOL_VERSION, canonical_json


MAX_ENVELOPE_BYTES = 48 * 1024 * 1024
MAX_PLAINTEXT_BYTES = 32 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_PLAINTEXT_BYTES + 16  # AES-GCM tag.
SERVICE_NAME = "MindCore Secure Sync v1"
HKDF_SALT = b"MindCore-Sync-HKDF-v1"
NONCE_INFO = b"MindCore-Sync-Nonce-v1"


class SyncSecurityError(RuntimeError):
    """Safe, content-free security error code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SecretBackend(Protocol):
    def get_secret(self, account: str) -> str | None: ...
    def set_secret(self, account: str, value: str) -> None: ...
    def delete_secret(self, account: str) -> None: ...


class NativeKeyringBackend:
    """Fail-closed adapter for OS-native keychain/credential stores."""

    _ALLOWED_MODULES = (
        "keyring.backends.macos", "keyring.backends.windows",
        "keyring.backends.secretservice", "keyring.backends.kwallet",
    )

    def __init__(self, service_name: str = SERVICE_NAME):
        self.service_name = service_name
        try:
            import keyring
            backend = keyring.get_keyring()
            module = type(backend).__module__.lower()
            priority = float(getattr(backend, "priority", 0))
            if priority <= 0 or not any(module.startswith(value) for value in self._ALLOWED_MODULES):
                raise SyncSecurityError("OS_KEYRING_UNAVAILABLE")
            self._keyring = keyring
        except SyncSecurityError:
            raise
        except Exception as error:
            raise SyncSecurityError("OS_KEYRING_UNAVAILABLE") from error

    def get_secret(self, account: str) -> str | None:
        try:
            return self._keyring.get_password(self.service_name, account)
        except Exception as error:
            raise SyncSecurityError("OS_KEYRING_UNAVAILABLE") from error

    def set_secret(self, account: str, value: str) -> None:
        try:
            self._keyring.set_password(self.service_name, account, value)
        except Exception as error:
            raise SyncSecurityError("OS_KEYRING_UNAVAILABLE") from error

    def delete_secret(self, account: str) -> None:
        try:
            self._keyring.delete_password(self.service_name, account)
        except Exception as error:
            # Deleting an already absent test/device identity is idempotent.
            if type(error).__name__ not in {"PasswordDeleteError", "PasswordNotFoundError"}:
                raise SyncSecurityError("OS_KEYRING_UNAVAILABLE") from error


@dataclass(frozen=True)
class SyncIdentity:
    device_id: str
    public_key: str
    fingerprint: str


@dataclass(frozen=True)
class InboundEnvelope:
    sender_device_id: str
    message_id: str
    sequence: int
    persona_id: str
    payload_json: str
    retry_pending: bool


def _uuid(value: Any, code: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise SyncSecurityError(code)
    try:
        if str(uuid.UUID(value)) != value:
            raise SyncSecurityError(code)
    except (ValueError, TypeError, AttributeError) as error:
        raise SyncSecurityError(code) from error
    return value


def _canonical_loads(value: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise SyncSecurityError("ENVELOPE_MALFORMED")
            result[key] = item
        return result
    try:
        return json.loads(value, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              SyncSecurityError("ENVELOPE_MALFORMED")))
    except SyncSecurityError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise SyncSecurityError("ENVELOPE_MALFORMED") from error


def _b64decode(value: Any, code: str, low: int, high: int) -> bytes:
    if not isinstance(value, str) or len(value) > ((high + 2) // 3) * 4 + 8:
        raise SyncSecurityError(code)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise SyncSecurityError(code) from error
    if not low <= len(decoded) <= high:
        raise SyncSecurityError(code)
    return decoded


def _hex(value: bytes) -> str:
    return value.hex()


class PersonaSyncSecurity:
    def __init__(self, state_dir: str | Path, secret_backend: SecretBackend | None = None):
        self.state_dir = Path(state_dir)
        self.security_path = self.state_dir / "sync" / "security.sqlite"
        self.secret_backend = secret_backend

    def _backend(self) -> SecretBackend:
        return self.secret_backend if self.secret_backend is not None else NativeKeyringBackend()

    def _connect(self) -> sqlite3.Connection:
        try:
            self.security_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.security_path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                existing_tables = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
                if existing_tables is not None:
                    connection.close()
                    raise SyncSecurityError("TRUST_STORE_CORRUPT")
            elif version != 1:
                connection.close()
                raise SyncSecurityError("TRUST_STORE_CORRUPT")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS identity_anchor(
                    slot INTEGER PRIMARY KEY CHECK(slot=1),
                    device_id TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trusted_peer(
                    device_id TEXT PRIMARY KEY,
                    public_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('TRUSTED','REVOKED')),
                    paired_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbound_sequence(
                    peer_device_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL CHECK(sequence>=0)
                );
                CREATE TABLE IF NOT EXISTS received_message(
                    sender_device_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence>0),
                    state TEXT NOT NULL CHECK(state IN ('PENDING','APPLIED')),
                    PRIMARY KEY(sender_device_id,message_id)
                );
                CREATE INDEX IF NOT EXISTS received_message_sequence
                    ON received_message(sender_device_id,sequence);
                CREATE TABLE IF NOT EXISTS peer_sequence(
                    sender_device_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL CHECK(sequence>=0)
                );
            """)
            if version == 0:
                connection.execute("PRAGMA user_version=1")
            return connection
        except SyncSecurityError:
            raise
        except sqlite3.Error as error:
            raise SyncSecurityError("TRUST_STORE_CORRUPT") from error

    @staticmethod
    def _generate_private_key() -> tuple[Any, bytes, bytes]:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            private = ec.generate_private_key(ec.SECP256R1())
            private_der = private.private_bytes(serialization.Encoding.DER,
                                                serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption())
            public_der = private.public_key().public_bytes(serialization.Encoding.DER,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo)
            return private, private_der, public_der
        except Exception as error:
            raise SyncSecurityError("SYNC_CRYPTO_UNAVAILABLE") from error

    @staticmethod
    def _decode_private(value: str) -> tuple[Any, bytes]:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            raw = base64.b64decode(value, validate=True)
            private = serialization.load_der_private_key(raw, password=None)
            if not isinstance(private, ec.EllipticCurvePrivateKey) \
                    or not isinstance(private.curve, ec.SECP256R1):
                raise ValueError("curve")
            public_der = private.public_key().public_bytes(serialization.Encoding.DER,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo)
            return private, public_der
        except Exception as error:
            raise SyncSecurityError("SYNC_KEY_INVALID") from error

    def identity(self) -> SyncIdentity:
        backend = self._backend()
        connection = self._connect()
        try:
            row = connection.execute("SELECT device_id,fingerprint FROM identity_anchor WHERE slot=1").fetchone()
            device_id = str(row["device_id"]) if row is not None else str(uuid.uuid4())
            _uuid(device_id, "DEVICE_ID_INVALID")
            secret = backend.get_secret(device_id)
            if secret is None:
                if row is not None:
                    # A missing key means this is a new cryptographic identity.
                    # Generate a new key, then invalidate inherited peer/replay state.
                    pass
                private, private_der, public_der = self._generate_private_key()
                secret = base64.b64encode(private_der).decode("ascii")
                backend.set_secret(device_id, secret)
            else:
                private, public_der = self._decode_private(secret)
            fingerprint = _hex(hashlib.sha256(public_der).digest())
            public_key = base64.b64encode(public_der).decode("ascii")
            if row is not None and (row["device_id"] != device_id or
                                    not hmac.compare_digest(str(row["fingerprint"]), fingerprint)):
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("UPDATE trusted_peer SET state='REVOKED'")
                connection.execute("DELETE FROM received_message")
                connection.execute("DELETE FROM peer_sequence")
                connection.execute("DELETE FROM outbound_sequence")
                connection.execute("UPDATE identity_anchor SET fingerprint=? WHERE slot=1",
                                   (fingerprint,))
                connection.commit()
            elif row is None:
                connection.execute("INSERT INTO identity_anchor(slot,device_id,fingerprint) VALUES(1,?,?)",
                                   (device_id, fingerprint))
            return SyncIdentity(device_id, public_key, fingerprint)
        except SyncSecurityError:
            raise
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            raise SyncSecurityError("SYNC_CRYPTO_UNAVAILABLE") from error
        finally:
            connection.close()

    @staticmethod
    def _public_key(encoded: str) -> Any:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            raw = _b64decode(encoded, "PEER_KEY_INVALID", 64, 512)
            value = serialization.load_der_public_key(raw)
            if not isinstance(value, ec.EllipticCurvePublicKey) \
                    or not isinstance(value.curve, ec.SECP256R1):
                raise ValueError("curve")
            return value, raw
        except SyncSecurityError:
            raise
        except Exception as error:
            raise SyncSecurityError("PEER_KEY_INVALID") from error

    def pairing_artifact(self) -> dict[str, Any]:
        identity = self.identity()
        return {"artifact_type": "mindcore_pairing", "device_id": identity.device_id,
                "fingerprint": identity.fingerprint, "protocol_version": PROTOCOL_VERSION,
                "public_key": identity.public_key}

    def pair(self, peer_artifact: str | dict[str, Any], confirmed_fingerprint: str) -> SyncIdentity:
        own = self.identity()
        artifact = _canonical_loads(peer_artifact) if isinstance(peer_artifact, str) else peer_artifact
        expected = {"artifact_type", "device_id", "fingerprint", "protocol_version", "public_key"}
        if not isinstance(artifact, dict) or set(artifact) != expected \
                or artifact.get("artifact_type") != "mindcore_pairing" \
                or artifact.get("protocol_version") != PROTOCOL_VERSION:
            raise SyncSecurityError("PAIRING_ARTIFACT_INVALID")
        peer_id = _uuid(artifact["device_id"], "DEVICE_ID_INVALID")
        if peer_id == own.device_id:
            raise SyncSecurityError("SELF_PAIRING_REJECTED")
        key, raw = self._public_key(artifact["public_key"])
        fingerprint = _hex(hashlib.sha256(raw).digest())
        if not hmac.compare_digest(fingerprint, str(artifact["fingerprint"])) \
                or not hmac.compare_digest(fingerprint, confirmed_fingerprint):
            raise SyncSecurityError("PAIRING_FINGERPRINT_MISMATCH")
        del key
        db = self._connect()
        try:
            db.execute("INSERT INTO trusted_peer(device_id,public_key,fingerprint,state,paired_at) "
                       "VALUES(?,?,?,'TRUSTED',?) ON CONFLICT(device_id) DO UPDATE SET "
                       "public_key=excluded.public_key,fingerprint=excluded.fingerprint,"
                       "state='TRUSTED',paired_at=excluded.paired_at",
                       (peer_id, artifact["public_key"], fingerprint, int(time.time() * 1000)))
        except sqlite3.Error as error:
            raise SyncSecurityError("TRUST_STORE_UNAVAILABLE") from error
        finally:
            db.close()
        return own

    def revoke(self, peer_device_id: str) -> None:
        peer_device_id = _uuid(peer_device_id, "DEVICE_ID_INVALID")
        db = self._connect()
        try:
            db.execute("UPDATE trusted_peer SET state='REVOKED' WHERE device_id=?", (peer_device_id,))
        finally:
            db.close()

    def peer_state(self, peer_device_id: str) -> str:
        peer_device_id = _uuid(peer_device_id, "DEVICE_ID_INVALID")
        db = self._connect()
        try:
            row = db.execute("SELECT public_key,fingerprint,state FROM trusted_peer WHERE device_id=?",
                             (peer_device_id,)).fetchone()
            if row is None:
                return "UNKNOWN"
            try:
                _, raw = self._public_key(str(row["public_key"]))
                actual = _hex(hashlib.sha256(raw).digest())
                if not hmac.compare_digest(actual, str(row["fingerprint"])) \
                        or row["state"] not in {"TRUSTED", "REVOKED"}:
                    return "CORRUPT"
            except SyncSecurityError:
                return "CORRUPT"
            return str(row["state"])
        finally:
            db.close()

    def trusted_devices(self) -> list[dict[str, Any]]:
        """Return public trust metadata for the product device list."""
        db = self._connect()
        try:
            return [{"device_id": str(row["device_id"]),
                     "fingerprint": str(row["fingerprint"]),
                     "state": str(row["state"]),
                     "paired_at": int(row["paired_at"])}
                    for row in db.execute(
                        "SELECT device_id,fingerprint,state,paired_at FROM trusted_peer "
                        "ORDER BY paired_at DESC")]
        finally:
            db.close()

    def _trusted_peer(self, peer_device_id: str) -> tuple[Any, str]:
        state = self.peer_state(peer_device_id)
        if state == "CORRUPT":
            raise SyncSecurityError("TRUST_STORE_CORRUPT")
        if state == "UNKNOWN":
            raise SyncSecurityError("UNKNOWN_PEER")
        if state != "TRUSTED":
            raise SyncSecurityError("REVOKED_PEER")
        db = self._connect()
        try:
            row = db.execute("SELECT public_key,fingerprint FROM trusted_peer WHERE device_id=?",
                             (peer_device_id,)).fetchone()
            assert row is not None
            peer_key, raw = self._public_key(str(row["public_key"]))
            fingerprint = _hex(hashlib.sha256(raw).digest())
            if not hmac.compare_digest(fingerprint, str(row["fingerprint"])):
                raise SyncSecurityError("TRUST_STORE_CORRUPT")
            return peer_key, fingerprint
        finally:
            db.close()

    @staticmethod
    def _derive_key(private: Any, peer_public: Any, persona_id: str,
                    sender: str, recipient: str, sender_fp: str, recipient_fp: str) -> bytes:
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            shared = private.exchange(ec.ECDH(), peer_public)
            info = (f"MindCore-Sync-v1|{persona_id}|{sender}|{recipient}|{sender_fp}|{recipient_fp}")\
                .encode("utf-8")
            return HKDF(algorithm=hashes.SHA256(), length=32, salt=HKDF_SALT,
                        info=info).derive(shared)
        except Exception as error:
            raise SyncSecurityError("SYNC_CRYPTO_UNAVAILABLE") from error

    @staticmethod
    def _nonce(key: bytes, sequence: int) -> bytes:
        prefix = hmac.new(key, NONCE_INFO, hashlib.sha256).digest()[:4]
        return prefix + struct.pack(">Q", sequence)

    @staticmethod
    def _aad(sender: str, recipient: str, persona_id: str,
             message_id: str, sequence: int) -> bytes:
        return f"1|{sender}|{recipient}|{persona_id}|{message_id}|{sequence}|persona_delta".encode("utf-8")

    def _private_for(self, identity: SyncIdentity) -> Any:
        secret = self._backend().get_secret(identity.device_id)
        if secret is None:
            raise SyncSecurityError("SYNC_CRYPTO_UNAVAILABLE")
        private, public_raw = self._decode_private(secret)
        if not hmac.compare_digest(_hex(hashlib.sha256(public_raw).digest()), identity.fingerprint):
            raise SyncSecurityError("SYNC_KEY_IDENTITY_MISMATCH")
        return private

    def _outbound_sequence(self, peer_id: str) -> int:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT sequence FROM outbound_sequence WHERE peer_device_id=?",
                             (peer_id,)).fetchone()
            sequence = 1 if row is None else int(row[0]) + 1
            if sequence > 0x7FFFFFFFFFFFFFFF:
                raise SyncSecurityError("OUTBOUND_SEQUENCE_EXHAUSTED")
            db.execute("INSERT INTO outbound_sequence(peer_device_id,sequence) VALUES(?,?) "
                       "ON CONFLICT(peer_device_id) DO UPDATE SET sequence=excluded.sequence",
                       (peer_id, sequence))
            db.commit()
            return sequence
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def encrypt(self, recipient_device_id: str, persona_id: str, payload_json: str) -> dict[str, Any]:
        recipient = _uuid(recipient_device_id, "DEVICE_ID_INVALID")
        persona = _uuid(persona_id, "PERSONA_ID_INVALID")
        identity = self.identity()
        peer_public, peer_fp = self._trusted_peer(recipient)
        try:
            parsed_payload = _canonical_loads(payload_json)
            plaintext = canonical_json(parsed_payload).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise SyncSecurityError("DELTA_MALFORMED") from error
        if len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise SyncSecurityError("PAYLOAD_SIZE_LIMIT")
        sequence = self._outbound_sequence(recipient)
        message_id = str(uuid.uuid4())
        key = self._derive_key(self._private_for(identity), peer_public, persona,
                               identity.device_id, recipient, identity.fingerprint, peer_fp)
        nonce = self._nonce(key, sequence)
        aad = self._aad(identity.device_id, recipient, persona, message_id, sequence)
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
        except Exception as error:
            raise SyncSecurityError("SYNC_CRYPTO_UNAVAILABLE") from error
        return {"ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "message_id": message_id, "nonce": base64.b64encode(nonce).decode("ascii"),
                "persona_id": persona, "protocol_version": PROTOCOL_VERSION,
                "recipient_device_id": recipient, "sender_device_id": identity.device_id,
                "sequence": sequence, "type": "persona_delta"}

    def _reserve(self, sender: str, message_id: str, sequence: int) -> bool:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT state,sequence FROM received_message "
                             "WHERE sender_device_id=? AND message_id=?",
                             (sender, message_id)).fetchone()
            if old is not None:
                if int(old["sequence"]) != sequence:
                    raise SyncSecurityError("REPLAY_REJECTED")
                if old["state"] == "APPLIED":
                    raise SyncSecurityError("REPLAY_REJECTED")
                db.commit()
                return True
            high = db.execute("SELECT sequence FROM peer_sequence WHERE sender_device_id=?",
                              (sender,)).fetchone()
            if high is not None and sequence <= int(high[0]):
                raise SyncSecurityError("STALE_SEQUENCE_REJECTED")
            pending = db.execute("SELECT MAX(sequence) FROM received_message "
                                 "WHERE sender_device_id=? AND state='PENDING'", (sender,)).fetchone()
            if pending is not None and pending[0] is not None and sequence <= int(pending[0]):
                raise SyncSecurityError("STALE_SEQUENCE_REJECTED")
            count = int(db.execute("SELECT COUNT(*) FROM received_message WHERE sender_device_id=?",
                                   (sender,)).fetchone()[0])
            if count >= 100_000:
                raise SyncSecurityError("REPLAY_STORE_LIMIT")
            db.execute("INSERT INTO received_message(sender_device_id,message_id,sequence,state) "
                       "VALUES(?,?,?,'PENDING')", (sender, message_id, sequence))
            db.commit()
            return False
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def decrypt_and_reserve(self, expected_persona_id: str,
                            raw_envelope: str | bytes | dict[str, Any]) -> InboundEnvelope:
        expected_persona = _uuid(expected_persona_id, "PERSONA_ID_INVALID")
        if isinstance(raw_envelope, bytes):
            if len(raw_envelope) > MAX_ENVELOPE_BYTES:
                raise SyncSecurityError("ENVELOPE_SIZE_LIMIT")
            try:
                raw_envelope = raw_envelope.decode("utf-8", errors="strict")
            except UnicodeError as error:
                raise SyncSecurityError("ENVELOPE_MALFORMED") from error
        if isinstance(raw_envelope, str):
            if len(raw_envelope.encode("utf-8")) > MAX_ENVELOPE_BYTES:
                raise SyncSecurityError("ENVELOPE_SIZE_LIMIT")
            envelope = _canonical_loads(raw_envelope)
        else:
            envelope = raw_envelope
            try:
                if len(canonical_json(envelope).encode("utf-8")) > MAX_ENVELOPE_BYTES:
                    raise SyncSecurityError("ENVELOPE_SIZE_LIMIT")
            except SyncSecurityError:
                raise
            except (TypeError, ValueError) as error:
                raise SyncSecurityError("ENVELOPE_MALFORMED") from error
        fields = {"ciphertext", "message_id", "nonce", "persona_id", "protocol_version",
                  "recipient_device_id", "sender_device_id", "sequence", "type"}
        if not isinstance(envelope, dict) or set(envelope) != fields:
            raise SyncSecurityError("ENVELOPE_FIELDS_UNSUPPORTED")
        version, sequence = envelope["protocol_version"], envelope["sequence"]
        if type(version) is not int or type(sequence) is not int:
            raise SyncSecurityError("ENVELOPE_FIELDS_INVALID")
        if version != PROTOCOL_VERSION:
            raise SyncSecurityError("UNSUPPORTED_PROTOCOL")
        sender = _uuid(envelope["sender_device_id"], "UUID_INVALID")
        recipient = _uuid(envelope["recipient_device_id"], "UUID_INVALID")
        persona = _uuid(envelope["persona_id"], "UUID_INVALID")
        message_id = _uuid(envelope["message_id"], "UUID_INVALID")
        if persona != expected_persona or recipient != self.identity().device_id:
            raise SyncSecurityError("WRONG_PERSONA_OR_RECIPIENT")
        if sequence < 1 or envelope["type"] != "persona_delta":
            raise SyncSecurityError("ENVELOPE_FIELDS_INVALID")
        peer_public, peer_fp = self._trusted_peer(sender)
        identity = self.identity()
        nonce = _b64decode(envelope["nonce"], "NONCE_INVALID", 12, 12)
        ciphertext = _b64decode(envelope["ciphertext"], "CIPHERTEXT_INVALID", 16,
                                MAX_CIPHERTEXT_BYTES)
        key = self._derive_key(self._private_for(identity), peer_public, persona, sender,
                               recipient, peer_fp, identity.fingerprint)
        expected_nonce = self._nonce(key, sequence)
        if not hmac.compare_digest(nonce, expected_nonce):
            raise SyncSecurityError("NONCE_SEQUENCE_MISMATCH")
        aad = self._aad(sender, recipient, persona, message_id, sequence)
        try:
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
            payload = plaintext.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise SyncSecurityError("PAYLOAD_UTF8_INVALID") from error
        except Exception as error:
            raise SyncSecurityError("AUTHENTICATION_FAILED") from error
        retry = self._reserve(sender, message_id, sequence)
        return InboundEnvelope(sender, message_id, sequence, persona, payload, retry)

    def complete_inbound(self, sender_device_id: str, message_id: str, sequence: int) -> None:
        sender = _uuid(sender_device_id, "DEVICE_ID_INVALID")
        message = _uuid(message_id, "MESSAGE_ID_INVALID")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE received_message SET state='APPLIED' "
                                "WHERE sender_device_id=? AND message_id=? AND sequence=? AND state='PENDING'",
                                (sender, message, sequence))
            if cursor.rowcount != 1:
                raise SyncSecurityError("PENDING_MESSAGE_MISSING")
            db.execute("INSERT INTO peer_sequence(sender_device_id,sequence) VALUES(?,?) "
                       "ON CONFLICT(sender_device_id) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                       (sender, sequence))
            db.commit()
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

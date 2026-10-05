"""Storage mode and identity binding for schema-24 Persona databases."""
from __future__ import annotations

from uuid import UUID


PERSONA_ID_KEY = "mindcore_persona_id"


class PersonaBindingError(RuntimeError):
    """Safe, product-level failure raised before a protected action."""

    def __init__(self, state: str):
        self.state = state
        super().__init__(state)


def storage_mode(database_url: str | None) -> str:
    """Classify a connection without exposing its host or credentials."""
    value = (database_url or "").strip()
    scheme = value.split(":", 1)[0].lower() if ":" in value else ""
    if scheme == "file":
        return "LOCAL_ONLY"
    if scheme in {"libsql", "https"}:
        return "SHARED"
    raise ValueError("persona_storage_mode_invalid")


async def bind_persona_identity(connection, persona_id: str) -> None:
    """Bind a schema_metadata identity once; reject cross-Persona DB reuse.

    The schema-24 key/value table is reused, so this introduces no schema fork.
    An absent key is claimed atomically. Existing durable identity is immutable.
    """
    try:
        canonical = str(UUID(persona_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("persona_id_invalid") from error
    if canonical != persona_id.lower():
        raise ValueError("persona_id_invalid")
    async with connection.transaction():
        # The unique metadata key is the compare-and-set. Never update an
        # existing value: concurrent P/Q clients can have only one winner.
        await connection.execute(
            "insert into schema_metadata(key,value) values($1,$2) "
            "on conflict(key) do nothing", PERSONA_ID_KEY, canonical,
        )
        owner = await connection.fetchval(
            "select value from schema_metadata where key=$1", PERSONA_ID_KEY
        )
        if str(owner) != canonical:
            raise ValueError("shared_persona_identity_mismatch")


async def validate_for_protected_action(pool, expected_persona_id: str | None) -> str:
    """Read or atomically claim the durable schema-24 Persona owner.

    Returns BOUND_MATCH or BOUND_NOW. Connection/query errors are deliberately
    collapsed to DB_UNAVAILABLE so callers never continue on a local fallback.
    """
    if not expected_persona_id:
        raise PersonaBindingError("DB_UNAVAILABLE")
    try:
        async with pool.acquire() as connection:
            owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", PERSONA_ID_KEY
            )
            legacy_owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", "android_persona_id"
            )
            expected = str(UUID(expected_persona_id))
            if expected != expected_persona_id.lower():
                raise PersonaBindingError("DB_UNAVAILABLE")
            if legacy_owner is not None and str(legacy_owner) != expected:
                raise PersonaBindingError("BOUND_MISMATCH")
            if owner is None:
                await bind_persona_identity(connection, expected_persona_id)
                return "BOUND_NOW"
            if str(owner) != expected:
                raise PersonaBindingError("BOUND_MISMATCH")
            return "BOUND_MATCH"
    except PersonaBindingError:
        raise
    except ValueError as error:
        if str(error) == "shared_persona_identity_mismatch":
            raise PersonaBindingError("BOUND_MISMATCH") from error
        raise PersonaBindingError("DB_UNAVAILABLE") from error
    except Exception as error:
        raise PersonaBindingError("DB_UNAVAILABLE") from error


async def inspect_binding_state(pool, expected_persona_id: str | None) -> str:
    """Read current binding state for status UI without claiming an owner."""
    if not expected_persona_id:
        return "UNBOUND"
    try:
        expected = str(UUID(expected_persona_id))
        if expected != expected_persona_id.lower():
            return "UNBOUND"
        async with pool.acquire() as connection:
            owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", PERSONA_ID_KEY
            )
            legacy_owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", "android_persona_id"
            )
        if owner is None:
            if legacy_owner is None:
                return "UNBOUND"
            return "BOUND_MATCH" if str(legacy_owner) == expected else "BOUND_MISMATCH"
        if legacy_owner is not None and str(legacy_owner) != expected:
            return "BOUND_MISMATCH"
        return "BOUND_MATCH" if str(owner) == expected else "BOUND_MISMATCH"
    except Exception:
        return "DB_UNAVAILABLE"

"""Storage mode and identity binding for schema-24 Persona databases."""
from __future__ import annotations

from uuid import UUID


PERSONA_ID_KEY = "mindcore_persona_id"


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
        owner = await connection.fetchval(
            "select value from schema_metadata where key=$1", PERSONA_ID_KEY
        )
        if owner is not None and str(owner) != canonical:
            raise ValueError("shared_persona_identity_mismatch")
        if owner is None:
            await connection.execute(
                "insert into schema_metadata(key,value) values($1,$2) "
                "on conflict(key) do nothing", PERSONA_ID_KEY, canonical,
            )
            owner = await connection.fetchval(
                "select value from schema_metadata where key=$1", PERSONA_ID_KEY
            )
            if str(owner) != canonical:
                raise ValueError("shared_persona_identity_mismatch")

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote

import asyncpg
from fastapi import Request

from app.config import Settings
from app.database.turso import TursoPool

logger = logging.getLogger("diana.database")


@dataclass(frozen=True)
class DatabaseConnectionInfo:
    user: str
    password: str | None
    host: str
    port: int | None
    database: str
    ssl: str | bool | None = None


def parse_supabase_db_url(database_url: str) -> DatabaseConnectionInfo:
    """
    Parse Supabase PostgreSQL URLs without relying on urllib's netloc parsing.

    Supabase passwords often contain reserved URL characters. If such a password
    is pasted into SUPABASE_DB_URL without percent-encoding, generic URL parsers
    can mistake part of the password for the host. We split from the right-most
    '@' and only then parse host/port/database.
    """
    url = database_url.strip()
    if "://" not in url:
        raise ValueError("SUPABASE_DB_URL must start with postgresql:// or postgres://")

    scheme, rest = url.split("://", 1)
    if scheme not in {"postgresql", "postgres"}:
        raise ValueError("SUPABASE_DB_URL must use postgresql:// or postgres://")

    if "@" not in rest:
        raise ValueError("SUPABASE_DB_URL must include user, password, host, and database.")

    userinfo, host_and_path = rest.rsplit("@", 1)
    if ":" in userinfo:
        user, password = userinfo.split(":", 1)
    else:
        user, password = userinfo, None

    path = ""
    query = ""
    if "/" in host_and_path:
        host_port, path_and_query = host_and_path.split("/", 1)
        if "?" in path_and_query:
            path, query = path_and_query.split("?", 1)
        else:
            path = path_and_query
    else:
        host_port = host_and_path

    if not host_port:
        raise ValueError("SUPABASE_DB_URL host is missing.")

    port: int | None = None
    if host_port.startswith("["):
        host, _, tail = host_port[1:].partition("]")
        if tail.startswith(":"):
            port = int(tail[1:])
    elif ":" in host_port:
        host, port_text = host_port.rsplit(":", 1)
        port = int(port_text)
    else:
        host = host_port

    database = path or "postgres"
    query_params = dict(parse_qsl(query, keep_blank_values=True))
    ssl = query_params.get("sslmode") or query_params.get("ssl")

    return DatabaseConnectionInfo(
        user=unquote(user),
        password=unquote(password) if password is not None else None,
        host=host,
        port=port,
        database=unquote(database),
        ssl=ssl,
    )


def normalize_ssl_mode(ssl_mode: str | bool | None) -> bool | str | None:
    if ssl_mode is None or isinstance(ssl_mode, bool):
        return ssl_mode

    normalized = ssl_mode.lower()
    if normalized in {"disable", "false", "0"}:
        return False
    if normalized in {"allow", "prefer", "require", "verify-ca", "verify-full", "true", "1"}:
        return True
    return ssl_mode


async def create_pool(settings: Settings) -> asyncpg.Pool | TursoPool | None:
    if settings.database_backend.lower() == "turso":
        if not settings.database_url or not settings.database_auth_token:
            return None
        return TursoPool(settings.database_url, settings.database_auth_token)
    if not settings.supabase_db_url:
        return None

    async def init_connection(connection: asyncpg.Connection) -> None:
        await connection.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        await connection.set_type_codec(
            "json",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    connection_info = parse_supabase_db_url(settings.supabase_db_url)
    connect_kwargs = {
        "user": connection_info.user,
        "password": connection_info.password,
        "host": connection_info.host,
        "port": connection_info.port,
        "database": connection_info.database,
    }
    if connection_info.ssl:
        connect_kwargs["ssl"] = normalize_ssl_mode(connection_info.ssl)
    connect_kwargs = {key: value for key, value in connect_kwargs.items() if value is not None}

    return await asyncpg.create_pool(
        **connect_kwargs,
        min_size=0,
        max_size=5,
        command_timeout=30,
        init=init_connection,
    )


async def close_pool(pool: asyncpg.Pool | TursoPool | None) -> None:
    if pool is not None:
        await pool.close()


async def get_pool(request: Request) -> AsyncIterator[asyncpg.Pool]:
    pool: asyncpg.Pool | None = request.app.state.db_pool
    if pool is None:
        settings: Settings = request.app.state.settings
        required = (
            "DATABASE_URL and DATABASE_AUTH_TOKEN"
            if settings.database_backend.lower() == "turso"
            else "SUPABASE_DB_URL"
        )
        raise RuntimeError(f"Database pool is not configured. Set {required}.")
    yield pool


async def check_database(pool: asyncpg.Pool | None, host: str | None = None, port: int | None = None) -> str:
    if pool is None:
        return "not_configured"

    try:
        async with pool.acquire() as connection:
            await connection.fetchval("select 1")
        return "connected"
    except Exception as exc:
        logger.error(
            "Database health check failed host=%s port=%s error_type=%s error_message=%s",
            host or "unknown",
            port or "unknown",
            type(exc).__name__,
            str(exc),
        )
        return "error"

import asyncpg
from fastapi import APIRouter, Request
import logging

from app.database.connection import check_database, parse_supabase_db_url
from app.services.error_safety import safe_database_diagnostic

router = APIRouter(tags=["health"])
logger = logging.getLogger("diana.database")


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    pool: asyncpg.Pool | None = request.app.state.db_pool
    settings = request.app.state.settings
    host = None
    port = None
    if settings.supabase_db_url:
        try:
            connection_info = parse_supabase_db_url(settings.supabase_db_url)
            host = connection_info.host
            port = connection_info.port
        except Exception as exc:
            diagnostic = safe_database_diagnostic(exc, settings.supabase_db_url)
            logger.error(
                "Database URL parse failed category=%s error_type=%s "
                "database_url_present=%s database_url_scheme=%s database_host_present=%s",
                diagnostic.category,
                diagnostic.error_type,
                str(diagnostic.url_present).lower(),
                diagnostic.url_scheme,
                str(diagnostic.host_present).lower(),
            )

    database_url = settings.database_url if settings.database_backend.lower() == "turso" else settings.supabase_db_url
    db_status = await check_database(pool, host=host, port=port, database_url=database_url)
    status = "ok" if db_status == "connected" else "degraded"
    return {"status": status, "db": db_status}

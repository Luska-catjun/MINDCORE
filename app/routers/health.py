import asyncpg
from fastapi import APIRouter, Request
import logging

from app.database.connection import check_database, parse_supabase_db_url

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
            logger.error(
                "Database URL parse failed error_type=%s error_message=%s",
                type(exc).__name__,
                str(exc),
            )

    db_status = await check_database(pool, host=host, port=port)
    status = "ok" if db_status == "connected" else "degraded"
    return {"status": status, "db": db_status}

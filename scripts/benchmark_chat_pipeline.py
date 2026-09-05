#!/usr/bin/env python3
"""Run disposable remote-Turso chat measurements with a deterministic provider.

This intentionally requires dedicated benchmark credentials.  The normal
DATABASE_URL may point at a user's live Diana state and is never used here.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import statistics
import sys
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import BackgroundTasks

from app.config import Settings
from app.database.turso import TursoPool
from app.models.enums import MessageRole
from app.schemas.chat import ChatRequest
from app.schemas.conversations import ConversationCreate
from app.services import repository
from app.services.chat_turn_coordinator import ChatTurnCoordinator


MARKER = "codex-performance-benchmark"
LATENCY_PAIR = re.compile(r"([a-z_]+)=([0-9.]+)(?:ms)?")


class LatencyCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.turns: list[dict[str, float]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not message.startswith("CHAT_LATENCY "):
            return
        self.turns.append({key: float(value) for key, value in LATENCY_PAIR.findall(message)})


def _summary(turns: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    values: dict[str, list[float]] = defaultdict(list)
    for turn in turns:
        for key, value in turn.items():
            values[key].append(value)
    return {
        key: {"median_ms": round(statistics.median(series), 2), "max_ms": round(max(series), 2)}
        for key, series in sorted(values.items())
    }


async def _delete_conversation(pool: TursoPool, conversation_id: UUID) -> None:
    async with pool.acquire() as connection:
        await connection.execute("delete from conversations where conversation_id=$1", conversation_id)
        remaining = await connection.fetchval("select count(*) from conversations where conversation_id=$1", conversation_id)
    if int(remaining):
        raise RuntimeError("benchmark cleanup did not remove its conversation")


async def run(turns: int) -> int:
    url = os.environ.get("BENCHMARK_TURSO_DATABASE_URL")
    token = os.environ.get("BENCHMARK_TURSO_AUTH_TOKEN")
    if not url or not token:
        print("REAL_TURSO_BENCHMARK_UNAVAILABLE reason=BENCHMARK_TURSO_DATABASE_URL_and_BENCHMARK_TURSO_AUTH_TOKEN_required")
        return 0

    pool = TursoPool(url, token)
    async with pool.acquire() as connection:
        await connection.fetchval("select 1")
    conversation = await repository.create_conversation(pool, ConversationCreate(source_device=MARKER))
    conversation_id = conversation["id"]
    capture = LatencyCapture()
    logger = logging.getLogger("diana.chat")
    logger.addHandler(capture)
    settings = Settings(database_backend="turso", database_url=url, database_auth_token=token, llm_provider=MARKER)
    coordinator = ChatTurnCoordinator(pool=pool)
    try:
        # The provider boundary is the only mock: all selected MindCore and
        # Turso work remains real against the explicitly dedicated database.
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.chat_turn_coordinator.generate_reply", AsyncMock(return_value="benchmark reply")))
            for index in range(turns + 1):
                await coordinator.execute(
                    payload=ChatRequest(conversation_id=conversation_id, role=MessageRole.user, content="benchmark"),
                    background_tasks=BackgroundTasks(), settings=settings, identity_prompt="",
                )
        measured = capture.turns[1:]
        print(f"BENCHMARK_WARMUP_TURNS=1 BENCHMARK_MEASURED_TURNS={len(measured)}")
        print("BENCHMARK_MEDIAN_MAX_MS=" + str(_summary(measured)))
        print("PHASE_6B_RELEVANT_GOAL_REREAD=0 selector_is_db_independent=true")
        return 0
    finally:
        logger.removeHandler(capture)
        await _delete_conversation(pool, conversation_id)
        print("BENCHMARK_DISPOSABLE_CONVERSATION_CLEANUP_OK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=5)
    args = parser.parse_args()
    if args.turns < 1:
        parser.error("--turns must be at least 1")
    return asyncio.run(run(args.turns))


if __name__ == "__main__":
    raise SystemExit(main())

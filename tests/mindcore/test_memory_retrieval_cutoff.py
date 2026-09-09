from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.memory_service import (
    MEMORY_RETRIEVAL_PAGE_SIZE,
    reinforce_recalled_memories,
    retrieve_relevant_memories,
)


class TrackingConnection(TursoConnection):
    def __init__(self, connection: libsql.Connection) -> None:
        super().__init__(connection)
        self.retrieval_fetch_sizes: list[int] = []

    async def fetch(self, statement: str, *args: object) -> list[dict]:
        rows = await super().fetch(statement, *args)
        if "from memories" in " ".join(statement.casefold().split()):
            self.retrieval_fetch_sizes.append(len(rows))
        return rows


class LocalMemoryPool:
    def __init__(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TrackingConnection(self.raw)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    def close(self) -> None:
        self.raw.close()


class StaticRowsPool:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, *_args: object) -> list[dict]:
        return self.rows


class MemoryRetrievalCutoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalMemoryPool()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                create table memories (
                    memory_id text primary key,
                    content text not null,
                    memory_type text not null,
                    importance real not null,
                    recall_frequency integer not null,
                    memory_strength real not null,
                    last_recalled_at text,
                    source_message_id text,
                    created_at text not null,
                    updated_at text not null
                )
                """
            )

    async def asyncTearDown(self) -> None:
        self.pool.close()

    async def _insert_memory(
        self,
        memory_id: str,
        content: str,
        *,
        importance: float,
        updated_at: datetime,
        source_message_id: str | None = "source-message",
    ) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                insert into memories (
                    memory_id, content, memory_type, importance,
                    recall_frequency, memory_strength, last_recalled_at,
                    source_message_id, created_at, updated_at
                ) values ($1, $2, 'user_fact', $3, 0, 1.0, null, $4, $5, $5)
                """,
                memory_id,
                content,
                importance,
                source_message_id,
                updated_at,
            )

    async def test_relevant_old_low_importance_memory_beyond_global_cutoff_is_recalled(self) -> None:
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        async with self.pool.connection.transaction():
            for index in range(500):
                await self._insert_memory(
                    f"unrelated-{index:04d}",
                    f"영화와 축구에 관한 관련 없는 기록 {index}",
                    importance=0.99,
                    updated_at=now - timedelta(minutes=index),
                )
            await self._insert_memory(
                "relevant-old-memory",
                "사용자의 자주 마시는 홍차 종류는 얼그레이다.",
                importance=0.01,
                updated_at=now - timedelta(days=400),
                source_message_id=None,
            )
        self.pool.connection.retrieval_fetch_sizes.clear()

        memories = await retrieve_relevant_memories(
            self.pool,
            "내가 자주 마시는 홍차 종류가 뭐였지?",
        )

        self.assertEqual(
            [memory["memory_id"] for memory in memories],
            ["relevant-old-memory"],
        )
        self.assertEqual(self.pool.connection.retrieval_fetch_sizes, [100, 100, 100, 100, 100, 1])
        self.assertLessEqual(
            max(self.pool.connection.retrieval_fetch_sizes),
            MEMORY_RETRIEVAL_PAGE_SIZE,
        )

    async def test_small_dataset_and_existing_weight_tie_break_remain_stable(self) -> None:
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        await self._insert_memory(
            "lower-weight",
            "사용자의 자주 마시는 홍차 종류는 다즐링이다.",
            importance=0.30,
            updated_at=now,
        )
        await self._insert_memory(
            "higher-weight",
            "사용자의 자주 마시는 홍차 종류는 얼그레이다.",
            importance=0.90,
            updated_at=now,
        )

        memories = await retrieve_relevant_memories(
            self.pool,
            "자주 마시는 홍차 종류가 뭐였지?",
            limit=2,
        )

        self.assertEqual(
            [memory["memory_id"] for memory in memories],
            ["higher-weight", "lower-weight"],
        )

    async def test_exact_ties_use_memory_id_as_a_deterministic_final_key(self) -> None:
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        for memory_id in ("tie-c", "tie-a", "tie-b"):
            await self._insert_memory(
                memory_id,
                "사용자의 반복해서 찾는 음료는 말차 라떼다.",
                importance=0.50,
                updated_at=now,
            )

        first = await retrieve_relevant_memories(self.pool, "찾는 음료가 뭐였지?", limit=2)
        second = await retrieve_relevant_memories(self.pool, "찾는 음료가 뭐였지?", limit=2)

        self.assertEqual([item["memory_id"] for item in first], ["tie-a", "tie-b"])
        self.assertEqual(
            [item["memory_id"] for item in second],
            ["tie-a", "tie-b"],
        )

    async def test_korean_unicode_and_punctuation_are_retrievable(self) -> None:
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        await self._insert_memory(
            "unicode-memory",
            "사용자의 카페 메뉴는 ‘말차-라떼’였다.",
            importance=0.40,
            updated_at=now,
        )

        memories = await retrieve_relevant_memories(self.pool, "말차/라떼 메뉴 기억나?")

        self.assertEqual([item["memory_id"] for item in memories], ["unicode-memory"])

    async def test_only_selected_memory_is_reinforced_once(self) -> None:
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        await self._insert_memory(
            "selected-memory",
            "사용자의 자주 마시는 홍차 종류는 얼그레이다.",
            importance=0.90,
            updated_at=now,
        )
        await self._insert_memory(
            "not-selected-memory",
            "사용자의 자주 마시는 홍차 종류는 다즐링이다.",
            importance=0.10,
            updated_at=now,
        )

        selected = await retrieve_relevant_memories(
            self.pool,
            "자주 마시는 홍차 종류가 뭐였지?",
            limit=1,
        )
        await reinforce_recalled_memories(self.pool, selected)

        async with self.pool.acquire() as connection:
            rows = await connection.fetch(
                "select memory_id, recall_frequency from memories order by memory_id"
            )
        frequencies = {str(row["memory_id"]): row["recall_frequency"] for row in rows}
        self.assertEqual(frequencies["selected-memory"], 1)
        self.assertEqual(frequencies["not-selected-memory"], 0)

    async def test_duplicate_candidates_are_deduplicated_before_selection(self) -> None:
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        row = {
            "memory_id": "same-memory",
            "content": "사용자의 자주 마시는 홍차 종류는 얼그레이다.",
            "memory_type": "user_fact",
            "importance": 0.50,
            "recall_frequency": 0,
            "memory_strength": 0.50,
            "last_recalled_at": None,
            "created_at": now,
            "updated_at": now,
        }

        memories = await retrieve_relevant_memories(
            StaticRowsPool([row, dict(row)]),
            "자주 마시는 홍차 종류가 뭐였지?",
        )

        self.assertEqual([item["memory_id"] for item in memories], ["same-memory"])


if __name__ == "__main__":
    unittest.main()

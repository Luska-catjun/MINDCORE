from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import libsql

from app.config import Settings
from app.database.turso import TursoConnection
from app.services.gemini import _call_gemini_sync
from app.services.memory_service import build_dynamic_context, retrieve_relevant_memories
from app.services.prompt_loader import load_persona_identity_prompt


class LocalPersonaPool:
    def __init__(self, path: Path) -> None:
        self.path = str(path)

    @asynccontextmanager
    async def acquire(self):
        raw = await asyncio.to_thread(libsql.connect, database=self.path)
        try:
            yield TursoConnection(raw)
        finally:
            await asyncio.to_thread(raw.close)


class MultiPersonaIsolationTests(unittest.TestCase):
    async def _initialize(self, pool: LocalPersonaPool, marker: str) -> None:
        now = datetime(2026, 9, 7, tzinfo=timezone.utc)
        async with pool.acquire() as connection:
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
                    created_at text not null,
                    updated_at text not null
                )
                """
            )
            await connection.execute(
                """
                insert into memories (
                    memory_id, content, memory_type, importance,
                    recall_frequency, memory_strength, created_at, updated_at
                ) values ($1, $2, 'semantic', 1.0, 0, 1.0, $3, $3)
                """,
                f"{marker}-id",
                marker,
                now,
            )

    def _provider_payload(
        self,
        settings: Settings,
        memories: list[dict],
    ) -> str:
        context = build_dynamic_context([], memories)
        identity = load_persona_identity_prompt(settings)
        with patch("app.services.gemini._request_gemini_sync", return_value="ok") as request:
            self.assertEqual(
                _call_gemini_sync(
                    settings,
                    "ONLY_MEMORY 기억을 말해줘",
                    dynamic_context=context,
                    identity_prompt=identity,
                ),
                "ok",
            )
        return str(request.call_args.args[1])

    def test_separate_databases_and_identity_snapshots_never_cross_prompts(self) -> None:
        with TemporaryDirectory(prefix="mindcore-personas-") as directory:
            root = Path(directory)
            identity_a = root / "jarvis.txt"
            identity_b = root / "nova.txt"
            identity_a.write_text("Jarvis identity", encoding="utf-8")
            identity_b.write_text("Nova identity", encoding="utf-8")
            pool_a = LocalPersonaPool(root / "a.db")
            pool_b = LocalPersonaPool(root / "b.db")
            asyncio.run(self._initialize(pool_a, "A_ONLY_MEMORY"))
            asyncio.run(self._initialize(pool_b, "B_ONLY_MEMORY"))

            settings_a = Settings(
                _env_file=None,
                persona_id="persona-a",
                persona_display_name="Jarvis",
                persona_identity_path=str(identity_a),
            )
            settings_b = Settings(
                _env_file=None,
                persona_id="persona-b",
                persona_display_name="Nova",
                persona_identity_path=str(identity_b),
            )
            memories_a = asyncio.run(retrieve_relevant_memories(pool_a, "A_ONLY_MEMORY"))
            memories_b = asyncio.run(retrieve_relevant_memories(pool_b, "B_ONLY_MEMORY"))
            payload_a = self._provider_payload(settings_a, memories_a)
            payload_b = self._provider_payload(settings_b, memories_b)

            self.assertIn("Jarvis", payload_a)
            self.assertIn("A_ONLY_MEMORY", payload_a)
            self.assertNotIn("Nova", payload_a)
            self.assertNotIn("B_ONLY_MEMORY", payload_a)
            self.assertIn("Nova", payload_b)
            self.assertIn("B_ONLY_MEMORY", payload_b)
            self.assertNotIn("Jarvis", payload_b)
            self.assertNotIn("A_ONLY_MEMORY", payload_b)

            # A fresh pool is equivalent to backend restart hydration for this
            # durable boundary; no process-local cache is needed to recover A.
            restarted_a = LocalPersonaPool(root / "a.db")
            recovered = asyncio.run(retrieve_relevant_memories(restarted_a, "A_ONLY_MEMORY"))
            self.assertEqual([item["content"] for item in recovered], ["A_ONLY_MEMORY"])

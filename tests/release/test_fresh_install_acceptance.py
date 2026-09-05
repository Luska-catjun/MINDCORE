"""Release acceptance: a blank official Turso baseline must run a real chat.

This deliberately uses the same baseline bootstrapper as a fresh install and
the FastAPI HTTP routes.  Only the network LLM boundary is mocked.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import AsyncMock, patch
from uuid import UUID

import libsql
from fastapi.testclient import TestClient

from app.config import Settings
from app.database.turso import TursoConnection
from app.main import create_app
from app.services import chat_turn_coordinator as coordinator
from app.services.mindcore.narrative import get_narrative_snapshot
from app.services.mindcore.self_model import get_self_model_snapshot
from app.services.mindcore.working_memory import clear_working_memory
from scripts.bootstrap_turso import bootstrap


class LocalFileTursoPool:
    """A real, file-backed libSQL acquire seam for lifecycle acceptance."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = str(database_path)

    @asynccontextmanager
    async def acquire(self):
        raw = await asyncio.to_thread(libsql.connect, database=self.database_path)
        try:
            yield TursoConnection(raw)
        finally:
            await asyncio.to_thread(raw.close)

    async def close(self) -> None:
        return None


class FreshInstallAcceptance(TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "fresh-install.db"
        self.pool = LocalFileTursoPool(self.database_path)
        self.assertEqual(asyncio.run(self._bootstrap()), "TURSO_BOOTSTRAP_OK version=21")
        self.assertEqual(asyncio.run(self._scalar("pragma integrity_check")), "ok")
        self.assertEqual(asyncio.run(self._rows("pragma foreign_key_check")), [])
        self.settings = Settings(
            private_access_password="fresh-test-password",
            auth_signing_secret="fresh-test-secret",
            database_backend="turso",
            database_url=str(self.database_path),
            database_auth_token="not-used-by-local-test-pool",
            llm_provider="gemini",
            gemini_api_key="not-used-by-mocked-provider",
        )
        self.contexts: list[str | None] = []

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def _bootstrap(self) -> str:
        async with self.pool.acquire() as connection:
            return await bootstrap(connection)

    async def _rows(self, statement: str, *args):
        async with self.pool.acquire() as connection:
            return await connection.fetch(statement, *args)

    async def _scalar(self, statement: str, *args):
        async with self.pool.acquire() as connection:
            return await connection.fetchval(statement, *args)

    def _app(self):
        return create_app(
            settings_override=self.settings,
            db_pool_factory=lambda _settings: self.pool,
        )

    def _login(self, client: TestClient) -> None:
        self.assertEqual(client.post("/auth/login", json={"password": "fresh-test-password"}).status_code, 200)

    def _post(self, client: TestClient, conversation_id: str, content: str) -> dict:
        response = client.post(
            "/chat",
            json={"conversation_id": conversation_id, "role": "user", "content": content},
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["user_message"]["conversation_id"], conversation_id)
        self.assertEqual(payload["diana_message"]["conversation_id"], conversation_id)
        self.assertIn("timestamp", payload["diana_message"])
        return payload

    def test_fresh_baseline_fastapi_chat_and_restart_recover_durable_skill_state(self) -> None:
        """Exercise bootstrap -> lifespan -> HTTP chat -> fresh lifespan recovery."""
        original_build_context = coordinator.build_context

        def capture_context(**kwargs):
            self.contexts.append(kwargs.get("skill_manual"))
            return original_build_context(**kwargs)

        async def deterministic_reply(_settings, user_text: str, **_kwargs) -> str:
            if "업다운" in user_text or user_text in {"업", "다운"}:
                return "그럼 업다운부터 할래."
            return "응, 그렇게 기억해둘게."

        # Retrieval is the only external-provider-adjacent pre-LLM boundary.
        # All repository, state, working-memory, experience, episode, knowledge,
        # decision, preference, and relationship code stays real against baseline.
        with patch.object(coordinator, "generate_reply", AsyncMock(side_effect=deterministic_reply)), patch.object(
            coordinator, "retrieve_relevant_memories", AsyncMock(return_value=[])
        ), patch.object(coordinator, "build_context", side_effect=capture_context):
            app = self._app()
            with TestClient(app) as client:
                self.assertEqual(get_narrative_snapshot(), ())
                self.assertEqual(get_self_model_snapshot(), ())
                self.assertEqual(client.get("/health").json(), {"status": "ok", "db": "connected"})
                self._login(client)
                created = client.post("/conversations", json={"source_device": "fresh-e2e"})
                self.assertEqual(created.status_code, 201, created.text)
                conversation_id = created.json()["id"]

                first = self._post(client, conversation_id, "오늘 좋다 ㅋㅋ 🙂")
                self.assertEqual(first["user_message"]["sequence"], 1)
                self.assertEqual(first["diana_message"]["sequence"], 2)
                self._post(client, conversation_id, "참고로 스파게티는 파스타의 한 종류야.")
                self._post(client, conversation_id, "나는 딸기 아이스크림을 좋아해.")
                self._post(client, conversation_id, "업다운 하자.")
                continuation = self._post(client, conversation_id, "업")
                self.assertEqual(continuation["diana_message"]["content"], "그럼 업다운부터 할래.")

                messages = client.get(f"/conversations/{conversation_id}/messages?limit=200&latest=true")
                self.assertEqual(messages.status_code, 200, messages.text)
                durable_messages = messages.json()
                self.assertEqual([message["sequence"] for message in durable_messages], list(range(1, 11)))
                self.assertEqual(durable_messages[0]["content"], "오늘 좋다 ㅋㅋ 🙂")

            # TestClient has closed the first lifespan.  Clear the process cache,
            # then construct a new app instance using only the same durable file.
            clear_working_memory(UUID(conversation_id))
            context_count_before_restart = len(self.contexts)
            app_after_restart = self._app()
            with TestClient(app_after_restart) as client:
                self.assertEqual(client.get("/health").json(), {"status": "ok", "db": "connected"})
                self._login(client)
                recovered = client.get(f"/conversations/{conversation_id}/messages?limit=200&latest=true")
                self.assertEqual(recovered.status_code, 200)
                self.assertEqual([message["sequence"] for message in recovered.json()], list(range(1, 11)))
                self._post(client, conversation_id, "다운")

        active_skill_rows = asyncio.run(self._rows(
            """select slot_type, item_key, status from diana_working_memory_items
               where conversation_id=$1 and slot_type='active_skill' and status='active'""",
            conversation_id,
        ))
        self.assertEqual(len(active_skill_rows), 1)
        self.assertEqual(active_skill_rows[0]["item_key"], "game.updown")
        self.assertTrue(any(context and "ACTIVE SKILL MANUAL" in context for context in self.contexts))
        self.assertTrue(any("업다운" in (context or "") for context in self.contexts[context_count_before_restart:]))

        # Bounded durable MindCore evidence: ordinary first-turn pipeline wrote
        # state and experience/episode records, while knowledge is queryable after
        # restart.  The test does not require every optional detector to fire.
        self.assertGreaterEqual(asyncio.run(self._scalar("select count(*) from diana_state")), 1)
        self.assertGreaterEqual(asyncio.run(self._scalar("select count(*) from experiences")), 1)
        self.assertGreaterEqual(asyncio.run(self._scalar("select count(*) from episodes")), 1)
        self.assertGreaterEqual(asyncio.run(self._scalar("select count(*) from diana_knowledge")), 1)
        self.assertGreaterEqual(asyncio.run(self._scalar("select count(*) from preferences")), 1)
        self.assertGreaterEqual(asyncio.run(self._scalar("select count(*) from preference_evidence")), 1)
        self.assertGreaterEqual(asyncio.run(self._scalar("select count(*) from decision_log")), 1)
        self.assertEqual(asyncio.run(self._scalar("select count(*) from messages where conversation_id=$1", conversation_id)), 12)
        self.assertEqual(asyncio.run(self._rows("pragma foreign_key_check")), [])
        self.assertEqual(asyncio.run(self._scalar("pragma integrity_check")), "ok")

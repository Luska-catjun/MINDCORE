from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.database.turso import TursoPool
from app.main import create_app


BASELINE = Path(__file__).resolve().parents[2] / "db" / "turso" / "baseline_v1.sql"


class IsolatedTestPool:
    """Mark the local file-backed pool as isolated from production recovery."""

    isolated = True

    def __init__(self, pool: TursoPool) -> None:
        self.pool = pool

    def acquire(self):
        return self.pool.acquire()


class AutonomyEventApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.pool = TursoPool(str(Path(self.temp.name) / "events.db"), "synthetic-test-token")
        async def bootstrap() -> None:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    for statement in BASELINE.read_text(encoding="utf-8").split(";"):
                        if statement.strip():
                            await connection.execute(statement)
        asyncio.run(bootstrap())
        self.scoped_pool = IsolatedTestPool(self.pool)
        self.settings = Settings(
            _env_file=None,
            private_access_password="synthetic-test-password",
            auth_signing_secret="synthetic-test-signing-secret",
            database_backend="turso",
            database_url="file::memory:",
            database_auth_token="synthetic-test-token",
            persona_id="persona-test",
            persona_display_name="Test Persona",
        )
        self.app = create_app(
            settings_override=self.settings,
            db_pool_factory=lambda _settings: self.scoped_pool,
        )

    def tearDown(self) -> None:
        asyncio.run(self.pool.close())
        self.temp.cleanup()

    def _insert_event(self, *, created_at: datetime) -> tuple[str, str]:
        conversation_id = str(uuid4())
        message_id = str(uuid4())
        turn_id = str(uuid4())
        async def insert() -> None:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await connection.execute(
                        """insert into conversations(conversation_id,source_device,started_at,ended_at)
                           values($1,'test',$2,null)""",
                        conversation_id, created_at,
                    )
                    await connection.execute(
                        """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                           values($1,$2,1,'diana','private content must not be returned','mindcore_proactive',$3)""",
                        message_id, conversation_id, created_at,
                    )
                    await connection.execute(
                        """insert into chat_turns(
                             turn_id,conversation_id,user_message_id,assistant_message_id,status,created_at,updated_at,
                             core_completed_at,completed_at,initiator_actor,trigger_type,input_source)
                           values($1,$2,null,$3,'complete',$4,$4,$4,$4,'persona','autonomy_decision','internal')""",
                        turn_id, conversation_id, message_id, created_at,
                    )
        asyncio.run(insert())
        return conversation_id, message_id

    def test_authenticated_incremental_cursor_returns_metadata_only_and_baselines_old_events(self) -> None:
        now = datetime.now(timezone.utc)
        old_conversation, _old_id = self._insert_event(created_at=now - timedelta(minutes=1))
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/autonomy/events").status_code, 401)
            self.assertEqual(client.post("/auth/login", json={"password": "synthetic-test-password"}).status_code, 200)
            baseline = client.get("/autonomy/events")
            self.assertEqual(baseline.status_code, 200)
            self.assertEqual(baseline.json()["events"], [])
            cursor = baseline.json()

            new_conversation, new_id = self._insert_event(created_at=now)
            incremental = client.get("/autonomy/events", params={
                "after_created_at": cursor["latest_created_at"],
                "after_message_id": cursor["latest_message_id"],
            })
            self.assertEqual(incremental.status_code, 200)
            body = incremental.json()
            self.assertEqual(len(body["events"]), 1)
            self.assertEqual(body["events"][0]["message_id"], new_id)
            self.assertEqual(body["events"][0]["conversation_id"], new_conversation)
            self.assertEqual(body["events"][0]["persona_id"], "persona-test")
            self.assertNotIn("content", body["events"][0])
            self.assertNotIn(old_conversation, str(body["events"]))
            self.assertEqual(
                client.get("/autonomy/events", params={"after_created_at": cursor["latest_created_at"]}).status_code,
                400,
            )


if __name__ == "__main__":
    unittest.main()

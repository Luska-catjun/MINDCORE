from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.database.turso import TursoPool
from app.main import create_app


BASELINE = Path("db/turso/baseline_v1.sql")


class MessageDeletionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "message-delete.db"
        self.pool = TursoPool(str(self.database_path), "test-token")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                for statement in BASELINE.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        await connection.execute(statement)
        self.settings = Settings(
            _env_file=None,
            private_access_password="test-password",
            auth_signing_secret="test-signing-secret",
            database_backend="turso",
            database_url=str(self.database_path),
            database_auth_token="test-token",
            llm_provider="gemini",
            gemini_api_key="test-key",
        )

    async def asyncTearDown(self) -> None:
        await self.pool.close()
        self.directory.cleanup()

    def _client(self) -> TestClient:
        return TestClient(create_app(
            settings_override=self.settings,
            db_pool_factory=lambda _settings: self.pool,
        ))

    @staticmethod
    def _login(client: TestClient) -> None:
        response = client.post("/auth/login", json={"password": "test-password"})
        if response.status_code != 200:
            raise AssertionError(response.text)

    async def _insert_conversation(self) -> str:
        conversation_id = str(uuid4())
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as connection:
            await connection.execute(
                "insert into conversations(conversation_id,source_device,started_at,ended_at) values($1,'test',$2,null)",
                conversation_id,
                now,
            )
        return conversation_id

    async def _insert_message(self, conversation_id: str, *, sequence: int, role: str) -> str:
        message_id = str(uuid4())
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
                   values($1,$2,$3,$4,$5,'test',$6)""",
                message_id,
                conversation_id,
                sequence,
                role,
                f"{role}-{sequence}",
                datetime.now(timezone.utc),
            )
        return message_id

    async def _insert_linked_turn(self, *, target_role: str) -> dict[str, str]:
        conversation_id = await self._insert_conversation()
        user_id = await self._insert_message(conversation_id, sequence=1, role="user")
        assistant_id = await self._insert_message(conversation_id, sequence=2, role="diana")
        target_id = user_id if target_role == "user" else assistant_id
        now = datetime.now(timezone.utc)
        experience_id = str(uuid4())
        episode_id = str(uuid4())
        memory_id = str(uuid4())
        knowledge_id = str(uuid4())
        knowledge_fact_id = str(uuid4())
        emotion_id = str(uuid4())
        relationship_id = str(uuid4())
        intention_id = str(uuid4())

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """insert into experiences(
                         experience_id,conversation_id,user_message_id,assistant_message_id,current_focus,
                         activated_memory_ids,state_before,state_after,state_changed,outcome_type,created_at)
                       values($1,$2,$3,$4,null,'[]','{}','{}',1,'conversation',$5)""",
                    experience_id, conversation_id, user_id, assistant_id, now,
                )
                await connection.execute(
                    """insert into episodes(
                         episode_id,conversation_id,sequence,summary,importance,emotional_impact,
                         personal_relevance,relationship_impact,novelty,confidence,recall_frequency,
                         memory_strength,decay,source_device,created_at,user_message_id,assistant_message_id,
                         experience_id,started_at,ended_at,episode_type,provenance,is_grounded,updated_at)
                       values($1,$2,1,'linked turn',.5,.2,.3,.1,.2,.8,0,.5,0,'test',$3,$4,$5,$6,$3,$3,'conversation','grounded_event',1,$3)""",
                    episode_id, conversation_id, now, user_id, assistant_id, experience_id,
                )
                await connection.execute(
                    """insert into memories(
                         memory_id,content,normalized_content,memory_type,importance,source_conversation_id,
                         source_message_id,created_at,updated_at,recall_frequency,memory_strength,last_recalled_at,source_episode_id)
                       values($1,'linked memory',$2,'episodic',.7,$3,$4,$5,$5,0,.7,null,$6)""",
                    memory_id, f"linked-memory-{memory_id}", conversation_id, target_id, now, episode_id,
                )
                await connection.execute(
                    """insert into diana_knowledge(
                         knowledge_id,subject_key,canonical_name,aliases,knowledge_type,summary,confidence,status,
                         source_type,source_id,source_episode_id,first_learned_at,last_reinforced_at,
                         reinforcement_count,created_at,updated_at,learning_session_count,last_learning_session_id)
                       values($1,$2,'Linked topic','[]','fact','linked fact',.8,'active','message',$3,$4,$5,$5,1,$5,$5,1,null)""",
                    knowledge_id, f"linked-topic-{knowledge_id}", target_id, episode_id, now,
                )
                await connection.execute(
                    """insert into diana_knowledge_facts(
                         knowledge_fact_id,knowledge_id,fact_key,fact_text,knowledge_scope,source_type,
                         source_message_id,source_episode_id,confidence,reinforcement_count,contradiction_count,
                         first_learned_at,last_reinforced_at,last_contradicted_at,created_at,updated_at)
                       values($1,$2,$3,'linked fact','user_provided','message',$4,$5,.8,1,0,$6,$6,null,$6,$6)""",
                    knowledge_fact_id, knowledge_id, f"fact-{knowledge_fact_id}", target_id, episode_id, now,
                )
                await connection.execute(
                    """insert into emotion_attributions(
                         emotion_attribution_id,emotion,delta,resulting_value,cause_type,cause_summary,
                         source_type,source_id,source_experience_id,confidence,created_at,episode_id)
                       values($1,'interest',.1,.6,'user_event','linked','message',$2,$3,.8,$4,$5)""",
                    emotion_id, target_id, experience_id, now, episode_id,
                )
                await connection.execute(
                    """insert into relationship_log(
                         relationship_log_id,source_experience_id,previous_state,delta,new_state,reason,created_at,episode_id)
                       values($1,$2,'{}','{}','{}','linked',$3,$4)""",
                    relationship_id, experience_id, now, episode_id,
                )
                await connection.execute(
                    """insert into diana_response_intentions(
                         id,conversation_id,user_message_id,assistant_message_id,action,target,reason_code,
                         confidence,source_refs,constraints,created_at)
                       values($1,$2,$3,$4,'acknowledge',null,'test',.8,'{}','[]',$5)""",
                    intention_id, conversation_id, user_id, assistant_id, now,
                )

        return {
            "conversation_id": conversation_id,
            "target_id": target_id,
            "other_id": assistant_id if target_role == "user" else user_id,
            "experience_id": experience_id,
            "episode_id": episode_id,
            "memory_id": memory_id,
            "knowledge_fact_id": knowledge_fact_id,
            "emotion_id": emotion_id,
            "relationship_id": relationship_id,
            "intention_id": intention_id,
        }

    async def test_delete_dependency_free_user_and_assistant_messages(self) -> None:
        with self._client() as client:
            self._login(client)
            for index, role in enumerate(("user", "diana"), start=1):
                conversation_id = await self._insert_conversation()
                message_id = await self._insert_message(conversation_id, sequence=index, role=role)
                response = client.delete(f"/messages/{message_id}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"id": message_id, "deleted": True})
                async with self.pool.acquire() as connection:
                    self.assertIsNone(await connection.fetchrow("select id from messages where id=$1", message_id))
                observation = client.get("/observe/messages?limit=100&offset=0")
                self.assertEqual(observation.status_code, 200)
                self.assertNotIn(message_id, {item["id"] for item in observation.json()["items"]})

    async def test_delete_cognition_linked_user_and_assistant_messages_preserves_provenance_policy(self) -> None:
        with self._client() as client:
            self._login(client)
            for target_role in ("user", "diana"):
                graph = await self._insert_linked_turn(target_role=target_role)
                response = client.delete(f"/messages/{graph['target_id']}")
                self.assertEqual(response.status_code, 200, response.text)

                async with self.pool.acquire() as connection:
                    self.assertIsNone(await connection.fetchrow("select id from messages where id=$1", graph["target_id"]))
                    self.assertIsNotNone(await connection.fetchrow("select id from messages where id=$1", graph["other_id"]))
                    self.assertIsNone(await connection.fetchrow("select experience_id from experiences where experience_id=$1", graph["experience_id"]))
                    episode = await connection.fetchrow(
                        "select user_message_id,assistant_message_id,experience_id from episodes where episode_id=$1",
                        graph["episode_id"],
                    )
                    self.assertIsNotNone(episode)
                    self.assertIsNone(episode["experience_id"])
                    self.assertIsNone(episode["user_message_id"] if target_role == "user" else episode["assistant_message_id"])
                    self.assertIsNone(await connection.fetchval("select source_message_id from memories where memory_id=$1", graph["memory_id"]))
                    self.assertIsNone(await connection.fetchval("select source_message_id from diana_knowledge_facts where knowledge_fact_id=$1", graph["knowledge_fact_id"]))
                    self.assertIsNone(await connection.fetchval("select source_experience_id from emotion_attributions where emotion_attribution_id=$1", graph["emotion_id"]))
                    self.assertIsNone(await connection.fetchval("select source_experience_id from relationship_log where relationship_log_id=$1", graph["relationship_id"]))
                    intention_column = "user_message_id" if target_role == "user" else "assistant_message_id"
                    self.assertIsNone(await connection.fetchval(
                        f"select {intention_column} from diana_response_intentions where id=$1",
                        graph["intention_id"],
                    ))
                    self.assertEqual(await connection.fetch("pragma foreign_key_check"), [])

                listed = client.get(
                    f"/conversations/{graph['conversation_id']}/messages?limit=200&offset=0&latest=true"
                )
                self.assertEqual(listed.status_code, 200)
                self.assertNotIn(graph["target_id"], {item["id"] for item in listed.json()})
                observed = client.get("/observe/messages?limit=100&offset=0")
                self.assertNotIn(graph["target_id"], {item["id"] for item in observed.json()["items"]})

    async def test_delete_nonexistent_message_returns_404(self) -> None:
        with self._client() as client:
            self._login(client)
            response = client.delete(f"/messages/{uuid4()}")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "Message not found.")

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import libsql
from app.database.turso import TursoConnection

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import chat_turn_coordinator as coordinator


class IsolatedPool:
    """Marker-only pool: all external DB services are patched in this ASGI gate."""
    isolated = True

class PersistentIsolatedPool:
    isolated = True
    def __init__(self):
        self.connection=TursoConnection(libsql.connect(':memory:')); asyncio.run(self._setup())
    @asynccontextmanager
    async def acquire(self): yield self.connection
    async def _setup(self):
        await self.connection.execute('create table conversations(conversation_id text primary key)')
        await self.connection.execute('create table messages(id text primary key,conversation_id text references conversations(conversation_id),role text,content text,created_at text)')
        for path in ('db/migrations/018_goals_needs_v01.sql','db/migrations/019_response_intentions_v01.sql'):
            for statement in Path(path).read_text().split(';'):
                if statement.strip(): await self.connection.execute(statement)


class AsgiChatGate(TestCase):
    def test_authenticated_chat_persists_pre_response_intention_with_exact_provenance(self):
        pool=PersistentIsolatedPool(); rows=[]; captured=[]; conversation_id=uuid4(); asyncio.run(pool.connection.execute('insert into conversations values($1)',conversation_id))
        settings=Settings(private_access_password='test-password',auth_signing_secret='test-secret',database_backend='turso',database_url='file::memory:',database_auth_token='test-token',llm_provider='gemini',gemini_api_key='test-key')
        async def save_message(_pool,payload):
            now=datetime.now(timezone.utc); row={'id':uuid4(),'conversation_id':payload.conversation_id,'role':payload.role,'content':payload.content,'sequence':len(rows)+1,'timestamp':now,'created_at':now,'source_device':'test','metadata':{}}; rows.append(row)
            await pool.connection.execute('insert into messages values($1,$2,$3,$4,$5)',row['id'],row['conversation_id'],row['role'],row['content'],now); return row
        def context(**kwargs):
            from app.services.mindcore.intentions import render_intention_context
            text=render_intention_context(kwargs['response_intention']);captured.append(text);return SimpleNamespace(dynamic_context=text,selected_memories=[])
        stack=ExitStack()
        knowledge_acquire = AsyncMock()
        patches={'repository.create_message':save_message,'get_internal_state':AsyncMock(return_value={}),'update_from_user_event':AsyncMock(return_value=SimpleNamespace(state={})),'get_recent_conversation_messages':AsyncMock(return_value=[]),'retrieve_relevant_memories':AsyncMock(return_value=[]),'check_epistemic_state':AsyncMock(return_value=([],None)),'get_relationship_state':AsyncMock(return_value=None),'get_stable_preferences':AsyncMock(return_value=[]),'get_diana_preferences':AsyncMock(return_value=[]),'get_context_emotion_attributions':AsyncMock(return_value=[]),'get_temporal_snapshot':AsyncMock(return_value=None),'build_context':context,'generate_reply':AsyncMock(return_value='요약 결과'),'reinforce_recalled_memories':AsyncMock(),'sanitize_decision_reason':AsyncMock(side_effect=lambda _p,c,_r:c),'record_decision':AsyncMock(),'extract_and_store_memory':AsyncMock(return_value=None),'record_experience':AsyncMock(return_value=None),'finalize_episode_linkage':AsyncMock(return_value=None),'acquire_user_knowledge':knowledge_acquire}
        for name,value in patches.items():
            if '.' in name: _,attr=name.split('.');stack.enter_context(patch.object(coordinator.repository,attr,value))
            else: stack.enter_context(patch.object(coordinator,name,value))
        app=create_app(settings_override=settings,db_pool_factory=lambda _s:pool)
        with stack,TestClient(app) as client:
            self._login(client); response=client.post('/chat',json={'conversation_id':str(conversation_id),'role':'user','content':'이 문장 요약해줘'}); self.assertEqual(response.status_code,201)
        self.assertIn('Primary action: fulfill_request.',captured[0]);self.assertNotIn('curiosity=',captured[0])
        knowledge_acquire.assert_awaited_once()
        self.assertIsNone(knowledge_acquire.await_args.kwargs['source_episode_id'])
        result=asyncio.run(pool.connection.fetchrow('select * from diana_response_intentions'))
        self.assertEqual(result['action'],'fulfill_request');self.assertEqual(str(result['conversation_id']),str(conversation_id));self.assertEqual(str(result['user_message_id']),str(rows[0]['id']));self.assertEqual(str(result['assistant_message_id']),str(rows[1]['id']))
    def test_production_turso_lifespan_does_not_require_supabase_url(self):
        settings = Settings(
            APP_ENV="production",
            private_access_password="test-password",
            auth_signing_secret="test-secret",
            database_backend="turso",
            database_url="file::memory:",
            database_auth_token="test-token",
            llm_provider="gemini",
            llm_fallback_provider=None,
            gemini_api_key="test-key",
            cors_origins=["https://example.test"],
        )
        app = create_app(settings_override=settings, db_pool_factory=lambda _settings: IsolatedPool())
        with TestClient(app):
            pass

    def _client(self, *, failing: str | None = None, narrative_fails: bool = False, self_model_fails: bool = False, world_model_fails: bool = False):
        rows: list[dict] = []; calls: list[str] = []; pool = IsolatedPool()
        settings = Settings(private_access_password="test-password", auth_signing_secret="test-secret", database_backend="turso", database_url="file::memory:", database_auth_token="test-token", llm_provider="gemini", gemini_api_key="test-key")
        async def save_message(_pool, payload):
            now=datetime.now(timezone.utc);row={"id":uuid4(),"conversation_id":payload.conversation_id,"sequence":len(rows)+1,"content":payload.content,"role":payload.role,"timestamp":now,"created_at":now,"source_device":"test","metadata":{}};rows.append(row);return row
        async def decision(*_a, **_k): calls.append("decision")
        async def experience(*_a, **_k): calls.append("experience");return {"experience_id":uuid4()}
        async def finalize(*_a, **_k): calls.append("episode");return {"episode_id":uuid4(),"provenance":"grounded_event"}
        async def fail(*_a, **_k): calls.append(failing or "failure");raise RuntimeError(failing or "failure")
        if failing == "decision": decision = fail
        if failing == "experience": experience = fail
        async def narrative(*_a, **_k):
            calls.append("narrative")
            if narrative_fails: raise RuntimeError("narrative-failure")
        async def self_model(*_a, **_k):
            calls.append("self-model")
            if self_model_fails: raise RuntimeError("self-model-failure")
        def world_model(*_a, **_k):
            if world_model_fails: raise RuntimeError("world-model-failure")
            return None
        ctx=SimpleNamespace(dynamic_context=None,selected_memories=[])
        patches={"repository.create_message":save_message,"get_internal_state":AsyncMock(return_value={}),"update_from_user_event":AsyncMock(return_value=SimpleNamespace(state={})),"get_recent_conversation_messages":AsyncMock(return_value=[]),"retrieve_relevant_memories":AsyncMock(return_value=[]),"check_epistemic_state":AsyncMock(return_value=([],None)),"get_relationship_state":AsyncMock(return_value=None),"get_stable_preferences":AsyncMock(return_value=[]),"get_diana_preferences":AsyncMock(return_value=[]),"get_context_emotion_attributions":AsyncMock(return_value=[]),"get_temporal_snapshot":AsyncMock(return_value=None),"build_context":lambda **_k:ctx,"generate_reply":AsyncMock(return_value="B가 궁금해!"),"reinforce_recalled_memories":AsyncMock(),"sanitize_decision_reason":AsyncMock(side_effect=lambda _p,c,_r:c),"record_decision":decision,"extract_and_store_memory":AsyncMock(return_value=None),"record_experience":experience,"finalize_episode_linkage":finalize,"link_attributions_to_experience":AsyncMock(),"update_relationship_from_experience":AsyncMock(),"update_preference_from_experience":AsyncMock(),"update_diana_preference_from_experience":AsyncMock(),"consolidate_recent_experience":AsyncMock(),"acquire_user_knowledge":AsyncMock(),"update_narratives_for_episode":narrative,"update_self_model_shadow":self_model}
        stack = ExitStack()
        create_pool_spy = Mock(
            side_effect=AssertionError("production pool must not be created")
        )
        stack.enter_context(patch("app.main.create_pool", create_pool_spy))
        for name,value in patches.items():
            if "." in name:
                _,attr=name.split(".");stack.enter_context(patch.object(coordinator.repository,attr,value))
            else: stack.enter_context(patch.object(coordinator,name,value))
        stack.enter_context(patch.object(coordinator,"update_working_memory",return_value=None));stack.enter_context(patch.object(coordinator,"update_world_model",side_effect=world_model));stack.enter_context(patch.object(coordinator,"update_goals",return_value=None));
        app=create_app(settings_override=settings,db_pool_factory=lambda _settings:pool)
        return TestClient(app), stack, rows, calls, pool, create_pool_spy

    def _login(self, client):
        self.assertEqual(client.post("/auth/login",json={"password":"test-password"}).status_code,200)

    def test_asgi_auth_chat_background_and_isolation(self):
        client,stack,rows,calls,pool,create_pool_spy=self._client()
        with stack,client:
            self.assertEqual(client.post("/chat",json={"conversation_id":str(uuid4()),"role":"user","content":"A, B, C 중 골라봐"}).status_code,401)
            self._login(client)
            response=client.post("/chat",json={"conversation_id":str(uuid4()),"role":"user","content":"A, B, C 중 골라봐"})
            self.assertEqual(response.status_code,201);self.assertEqual(len(rows),2);self.assertTrue(pool.isolated);self.assertIn("narrative",calls);self.assertIn("self-model",calls)
            self.assertEqual(create_pool_spy.call_count, 0)

    def test_asgi_self_model_failure_is_caught(self):
        client,stack,rows,calls,_pool,create_pool_spy=self._client(self_model_fails=True)
        with stack,client:
            self._login(client)
            with self.assertLogs("diana.chat", level="WARNING") as captured:
                response=client.post("/chat",json={"conversation_id":str(uuid4()),"role":"user","content":"A, B, C 중 골라봐"})
            self.assertEqual(response.status_code,201);self.assertEqual(len(rows),2);self.assertIn("self-model",calls)
            self.assertEqual(create_pool_spy.call_count, 0)
            self.assertTrue(any("Self model shadow update skipped" in line for line in captured.output))

    def test_asgi_world_model_failure_keeps_chat_success(self):
        client,stack,rows,_calls,_pool,create_pool_spy=self._client(world_model_fails=True)
        with stack,client:
            self._login(client)
            with self.assertLogs("diana.chat", level="WARNING") as captured:
                response=client.post("/chat",json={"conversation_id":str(uuid4()),"role":"user","content":"좋은 아침"})
            self.assertEqual(response.status_code,201);self.assertEqual(len(rows),2)
            self.assertEqual(create_pool_spy.call_count, 0)
            self.assertTrue(any("World Model update skipped" in line for line in captured.output))

    def test_asgi_world_model_observation_is_read_only_and_ephemeral(self):
        client,stack,_rows,_calls,_pool,create_pool_spy=self._client()
        with stack,client:
            self._login(client)
            response = client.get("/observe/world-model")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["storage"], "ephemeral")
            self.assertEqual(payload["weather"]["status"], "unknown")
            self.assertIn("day_period", {item["key"] for item in payload["temporal_facts"]})
            self.assertEqual(create_pool_spy.call_count, 0)

    def test_asgi_decision_and_experience_failures_keep_http_success(self):
        for failure in ("decision","experience"):
            client,stack,rows,calls,_pool,create_pool_spy=self._client(failing=failure)
            with self.subTest(failure=failure),stack,client:
                self._login(client);response=client.post("/chat",json={"conversation_id":str(uuid4()),"role":"user","content":"A, B, C 중 골라봐"})
                self.assertEqual(response.status_code,201);self.assertEqual(len(rows),2);self.assertIn(failure,calls)
                self.assertEqual(create_pool_spy.call_count, 0)

    def test_asgi_narrative_failure_is_caught(self):
        client,stack,rows,calls,_pool,create_pool_spy=self._client(narrative_fails=True)
        with stack,client:
            self._login(client)
            with self.assertLogs("diana.chat", level="WARNING") as captured:
                response=client.post("/chat",json={"conversation_id":str(uuid4()),"role":"user","content":"A, B, C 중 골라봐"})
            self.assertEqual(response.status_code,201);self.assertEqual(len(rows),2);self.assertIn("narrative",calls)
            self.assertEqual(create_pool_spy.call_count, 0)
            self.assertTrue(any("Narrative shadow update skipped" in line for line in captured.output))

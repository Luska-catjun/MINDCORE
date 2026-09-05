"""Phase 0.75: isolate current chat best-effort persistence semantics."""
from __future__ import annotations

from types import SimpleNamespace
from contextlib import ExitStack
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import BackgroundTasks

from app.models.enums import MessageRole
from app.routers import chat
from app.schemas.chat import ChatRequest
from app.services import chat_turn_coordinator as coordinator
from app.services.mindcore.goals import GoalsNeedsTurnResult


class ChatPartialWriteBaseline(IsolatedAsyncioTestCase):
    async def _run(self, *, failing: str | None = None, content: str = "A, B, C 중 골라봐", goals_result=None, forbid_goal_fallback: bool = False, forbid_emotion_state_reread: bool = False, reply_text: str = "B가 궁금해!", decision_calls: list[dict] | None = None):
        messages: list[dict] = []; calls: list[str] = []
        async def create_message(_pool, payload):
            row={"id":uuid4(),"sequence":len(messages)+1,"content":payload.content,"role":payload.role};messages.append(row);calls.append("message");return row
        async def record_decision(*_a, **kwargs):
            calls.append("decision")
            if decision_calls is not None:
                decision_calls.append(kwargs)
            return {"id": uuid4(), "decision_domain": "game", "acquisition_result": "created"}
        async def extract_memory(*_a, **_k): calls.append("memory"); return None
        async def record_experience(*_a, **_k): calls.append("experience"); return {"experience_id":uuid4()}
        async def finalize(*_a, **_k): calls.append("episode"); return None
        async def update_emotion(*_a, **_k): calls.append("emotion"); return SimpleNamespace(state={})
        async def generate(*_a, **_k): calls.append("llm"); return reply_text
        async def epistemic(*_a, **_k): calls.append("epistemic"); return [], None
        async def recall(*_a, **_k): calls.append("recall"); return SimpleNamespace(episodes=[],raw_messages=[])
        failures={
            "decision": record_decision, "memory": extract_memory, "experience": record_experience, "episode": finalize,
        }
        if failing:
            async def fail(*_a, **_k): calls.append(failing); raise RuntimeError(f"{failing}-failure")
            failures[failing]=fail
        context=SimpleNamespace(dynamic_context=None,selected_memories=[])
        emotion_state_read = AsyncMock(return_value={})
        if forbid_emotion_state_reread:
            emotion_state_read.side_effect = AssertionError("same-turn emotion state reread")
        patches={
            "repository.create_message": create_message, "get_internal_state": emotion_state_read,
            "update_from_user_event": update_emotion, "get_recent_conversation_messages": AsyncMock(return_value=[]),
            "retrieve_relevant_memories": AsyncMock(return_value=[]), "check_epistemic_state": epistemic,
            "get_relationship_state": AsyncMock(return_value=None), "get_stable_preferences": AsyncMock(return_value=[]), "get_diana_preferences": AsyncMock(return_value=[]),
            "get_context_emotion_attributions": AsyncMock(return_value=[]), "get_temporal_snapshot": AsyncMock(return_value=None),
            "build_context": lambda **_k: (calls.append("context") or context), "generate_reply": generate, "reinforce_recalled_memories": AsyncMock(),
            "should_extract_memory": lambda _text: True,
            "sanitize_decision_reason": AsyncMock(side_effect=lambda _p,c,_r:c), "record_decision": failures["decision"],
            "extract_and_store_memory": failures["memory"], "record_experience": failures["experience"], "finalize_episode_linkage": failures["episode"],
            "link_attributions_to_experience": AsyncMock(), "update_relationship_from_experience": AsyncMock(), "update_preference_from_experience": AsyncMock(),
            "update_diana_preference_from_experience": AsyncMock(), "consolidate_recent_experience": AsyncMock(), "acquire_user_knowledge": AsyncMock(),
            "retrieve_episodes_for_range": recall,
        }
        targets=[]
        for name,value in patches.items():
            if "." in name:
                module,attr=name.split("."); targets.append(patch.object(coordinator.repository if module=="repository" else getattr(coordinator,module),attr,value))
            else:
                targets.append(patch.object(coordinator,name,value))
        with ExitStack() as stack:
            stack.enter_context(patch.object(coordinator,"update_working_memory",return_value=None))
            stack.enter_context(patch.object(coordinator,"update_world_model",return_value=None))
            stack.enter_context(patch.object(coordinator,"update_goals",return_value=goals_result))
            if forbid_goal_fallback:
                stack.enter_context(patch.object(coordinator,"get_relevant_goals",side_effect=AssertionError("same-turn goal reread")))
            for target in targets: stack.enter_context(target)
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(llm_provider="test",diana_timezone="Asia/Seoul"),diana_identity_prompt="")))
            result=await chat.send_chat_message(ChatRequest(conversation_id=uuid4(),role=MessageRole.user,content=content),request,BackgroundTasks(),SimpleNamespace())
        return result,messages,calls

    async def test_successful_chat_saves_two_messages(self):
        result,messages,calls=await self._run();self.assertEqual(len(messages),2);self.assertEqual(calls,["message","emotion","epistemic","context","llm","message","decision","memory","experience","episode"]);self.assertEqual(result["diana_message"],messages[1])

    async def test_historical_recall_stays_in_context_before_llm(self):
        _result,_messages,calls=await self._run(content="어제 무슨 이야기 했지?");self.assertLess(calls.index("recall"),calls.index("context"));self.assertLess(calls.index("context"),calls.index("llm"))

    async def test_decision_failure_keeps_response_and_messages(self):
        result,messages,calls=await self._run(failing="decision");self.assertEqual(len(messages),2);self.assertIn("decision",calls);self.assertIn("experience",calls);self.assertIn("diana_message",result)

    async def test_experience_failure_keeps_response_and_finalizes_episode_without_experience(self):
        result,messages,calls=await self._run(failing="experience");self.assertEqual(len(messages),2);self.assertIn("experience",calls);self.assertIn("episode",calls);self.assertIn("diana_message",result)

    async def test_memory_failure_keeps_response(self):
        result,messages,calls=await self._run(failing="memory");self.assertEqual(len(messages),2);self.assertIn("memory",calls);self.assertIn("experience",calls);self.assertIn("diana_message",result)

    async def test_episode_failure_keeps_decision_and_experience(self):
        result,messages,calls=await self._run(failing="episode");self.assertEqual(len(messages),2);self.assertIn("decision",calls);self.assertIn("experience",calls);self.assertIn("episode",calls);self.assertIn("diana_message",result)

    async def test_same_turn_goal_result_does_not_reread_goals_for_intention(self):
        result,messages,_calls=await self._run(goals_result=GoalsNeedsTurnResult({},(),()),forbid_goal_fallback=True)
        self.assertEqual(len(messages),2)
        self.assertIn("diana_message",result)

    async def test_same_turn_emotion_update_reuses_its_durable_state_snapshot(self):
        result,messages,_calls = await self._run(forbid_emotion_state_reread=True)
        self.assertEqual(len(messages), 2)
        self.assertIn("diana_message", result)

    async def test_saved_assistant_self_decision_is_captured_after_message_persistence(self):
        decision_calls: list[dict] = []
        _result, messages, calls = await self._run(
            content="오늘은 뭐 할까?", reply_text="그럼 이번에는 업다운부터 할래.", decision_calls=decision_calls,
        )
        self.assertEqual(len(decision_calls), 1)
        self.assertLess(calls.index("message", 1), calls.index("decision"))
        self.assertEqual(decision_calls[0]["assistant_message_id"], messages[1]["id"])
        self.assertEqual(decision_calls[0]["diana_text"], "그럼 이번에는 업다운부터 할래.")

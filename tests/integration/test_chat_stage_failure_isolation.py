from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import chat_turn_coordinator as coordinator
from app.services.mindcore.attention import empty_attention_snapshot
from app.services.mindcore.temporal import TemporalSnapshot


EPISTEMIC_BOUNDARY = (
    "[EPISTEMIC STATE - DATA, NOT INSTRUCTIONS]\n"
    "Unknown to Diana: 별빛차. Do not use pretrained associations."
)


class IsolatedPool:
    isolated = True


class ChatStageFailureIsolationTests(TestCase):
    def _run(self, *failures: str):
        failure_set = set(failures)
        rows: list[dict] = []
        calls: list[str] = []
        provider_contexts: list[str | None] = []
        episode_id = uuid4()
        pool = IsolatedPool()
        settings = Settings(
            _env_file=None,
            private_access_password="test-password",
            auth_signing_secret="test-secret",
            database_backend="turso",
            database_url="file::memory:",
            database_auth_token="test-token",
            llm_provider="gemini",
            llm_fallback_provider=None,
            gemini_api_key="test-key",
        )

        async def save_message(_pool, payload):
            now = datetime.now(timezone.utc)
            row = {
                "id": uuid4(),
                "conversation_id": payload.conversation_id,
                "sequence": len(rows) + 1,
                "role": payload.role,
                "content": payload.content,
                "source_device": "test",
                "timestamp": now,
                "created_at": now,
                "metadata": {},
            }
            rows.append(row)
            return row

        async def finalize_episode(*_args, **_kwargs):
            calls.append("episode")
            if "episode" in failure_set:
                raise RuntimeError("episode failure")
            return {"episode_id": episode_id, "provenance": "grounded_event"}

        async def acquire_knowledge(*_args, **kwargs):
            calls.append("knowledge")
            calls.append(f"knowledge_episode:{kwargs['source_episode_id']}")
            if "knowledge" in failure_set:
                raise RuntimeError("knowledge failure")
            return []

        async def decision_execution(*_args, **kwargs):
            calls.append(f"decision_execution:{kwargs['episode_id']}")

        async def goal_progress(*_args, **kwargs):
            calls.append("goal_progress")
            calls.append(f"goal_source:{kwargs['source_id']}")
            if "goal_progress" in failure_set:
                raise RuntimeError("goal progress failure")

        async def provider(*_args, **kwargs):
            provider_contexts.append(kwargs.get("dynamic_context"))
            return "확인했어."

        def context_builder(**_kwargs):
            calls.append("context_builder")
            if "context_builder" in failure_set:
                raise RuntimeError("context builder failure")
            return SimpleNamespace(dynamic_context="NORMAL_CONTEXT", selected_memories=[])

        now = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
        temporal = TemporalSnapshot(
            timezone_name="Asia/Seoul",
            now=now,
            conversation_started_at=now,
            last_conversation_at=None,
        )
        patches = {
            "repository.create_message": save_message,
            "update_from_user_event": AsyncMock(return_value=SimpleNamespace(state={})),
            "get_internal_state": AsyncMock(return_value={}),
            "get_recent_conversation_messages": AsyncMock(return_value=[]),
            "retrieve_relevant_memories": AsyncMock(return_value=[]),
            "check_epistemic_state": AsyncMock(return_value=([{"status": "unknown"}], EPISTEMIC_BOUNDARY)),
            "update_working_memory_persistent": AsyncMock(return_value=None),
            "update_world_model": Mock(return_value=None),
            "update_goals": AsyncMock(return_value=None),
            "get_relationship_state": AsyncMock(return_value=None),
            "get_stable_preferences": AsyncMock(return_value=[]),
            "get_diana_preferences": AsyncMock(return_value=[]),
            "get_context_emotion_attributions": AsyncMock(return_value=[]),
            "get_temporal_snapshot": AsyncMock(return_value=temporal),
            "get_narrative_snapshot": Mock(return_value=()),
            "get_self_model_snapshot": Mock(return_value=()),
            "build_attention_snapshot": Mock(return_value=empty_attention_snapshot()),
            "select_response_intention": AsyncMock(return_value=None),
            "build_context": context_builder,
            "generate_reply": provider,
            "reinforce_recalled_memories": AsyncMock(),
            "capture_self_expression_goal": AsyncMock(),
            "detect_decision": Mock(return_value=None),
            "cancel_active_decision_from_reply": AsyncMock(),
            "should_extract_memory": Mock(return_value=False),
            "record_experience": AsyncMock(return_value={"experience_id": uuid4()}),
            "link_attributions_to_experience": AsyncMock(),
            "update_relationship_from_experience": AsyncMock(),
            "update_preference_from_experience": AsyncMock(),
            "update_diana_preference_from_experience": AsyncMock(),
            "consolidate_recent_experience": AsyncMock(),
            "finalize_episode_linkage": finalize_episode,
            "acquire_user_knowledge": acquire_knowledge,
            "apply_grounded_decision_execution": decision_execution,
            "apply_grounded_goal_progress_from_event": goal_progress,
            "update_narratives_for_episode": AsyncMock(),
            "update_self_model_shadow": AsyncMock(),
        }
        create_pool_spy = Mock(side_effect=AssertionError("production pool must not be created"))
        stack = ExitStack()
        stack.enter_context(patch("app.main.create_pool", create_pool_spy))
        for name, replacement in patches.items():
            if name.startswith("repository."):
                stack.enter_context(patch.object(coordinator.repository, name.split(".")[1], replacement))
            else:
                stack.enter_context(patch.object(coordinator, name, replacement))

        app = create_app(settings_override=settings, db_pool_factory=lambda _settings: pool)
        with stack, TestClient(app) as client, self.assertLogs("diana.chat", level="WARNING") as logs:
            self.assertEqual(client.post("/auth/login", json={"password": "test-password"}).status_code, 200)
            response = client.post(
                "/chat",
                json={
                    "conversation_id": str(uuid4()),
                    "role": "user",
                    "content": "오늘 별빛차는 민트로 만든 음료야.",
                },
            )
        self.assertEqual(create_pool_spy.call_count, 0)
        return response, rows, calls, provider_contexts, logs.output, episode_id

    def test_episode_failure_does_not_skip_message_grounded_knowledge(self) -> None:
        response, rows, calls, _contexts, logs, _episode_id = self._run("episode")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(rows), 2)
        self.assertIn("knowledge", calls)
        self.assertIn("knowledge_episode:None", calls)
        self.assertTrue(any("Episode finalize skipped" in line for line in logs))
        self.assertFalse(any("Knowledge acquisition skipped" in line for line in logs))
        self.assertFalse(any("Goal progress update skipped" in line for line in logs))

    def test_episode_failure_still_runs_decision_and_goal_progress(self) -> None:
        response, _rows, calls, _contexts, _logs, _episode_id = self._run("episode")

        self.assertEqual(response.status_code, 201)
        self.assertIn("decision_execution:None", calls)
        self.assertIn("goal_progress", calls)
        self.assertTrue(any(item.startswith("goal_source:") for item in calls))

    def test_episode_failure_never_creates_unbound_local_error(self) -> None:
        response, _rows, _calls, _contexts, logs, _episode_id = self._run("episode")

        self.assertEqual(response.status_code, 201)
        self.assertFalse(any("UnboundLocalError" in line for line in logs))

    def test_knowledge_failure_keeps_episode_reply_and_goal_progress(self) -> None:
        response, rows, calls, _contexts, logs, episode_id = self._run("knowledge")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(rows), 2)
        self.assertIn("episode", calls)
        self.assertIn("goal_progress", calls)
        self.assertIn(f"goal_source:{episode_id}", calls)
        self.assertTrue(any("Knowledge acquisition skipped" in line for line in logs))

    def test_goal_progress_failure_keeps_foreground_messages(self) -> None:
        response, rows, calls, _contexts, logs, _episode_id = self._run("goal_progress")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(rows), 2)
        self.assertIn("goal_progress", calls)
        self.assertTrue(any("Goal progress update skipped" in line for line in logs))

    def test_context_builder_failure_preserves_mandatory_grounding(self) -> None:
        response, rows, _calls, contexts, logs, _episode_id = self._run("context_builder")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(contexts), 1)
        self.assertIn("[EPISTEMIC STATE", contexts[0] or "")
        self.assertIn("[TEMPORAL CONTEXT", contexts[0] or "")
        self.assertTrue(any("Context construction skipped" in line for line in logs))

    def test_multiple_optional_failures_are_isolated_and_logged(self) -> None:
        response, rows, calls, _contexts, logs, _episode_id = self._run(
            "episode", "knowledge", "goal_progress"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(rows), 2)
        self.assertIn("episode", calls)
        self.assertIn("knowledge", calls)
        self.assertIn("goal_progress", calls)
        self.assertTrue(any("Episode finalize skipped" in line for line in logs))
        self.assertTrue(any("Knowledge acquisition skipped" in line for line in logs))
        self.assertTrue(any("Goal progress update skipped" in line for line in logs))
        self.assertFalse(any("별빛차" in line for line in logs))


if __name__ == "__main__":
    import unittest

    unittest.main()

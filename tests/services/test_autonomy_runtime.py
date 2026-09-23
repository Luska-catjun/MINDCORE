from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.autonomy_decision import ActionClass
from app.models.proactive_execution import ProactiveExecutionResult
from app.services.mindcore.autonomy_runtime import (
    AutonomyRuntimeState,
    DurableAutonomyState,
    effective_cooldown_seconds,
    is_quiet_time,
    load_durable_autonomy_state,
    run_autonomy_cycle,
)


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class FakeConnection:
    def __init__(self) -> None:
        self.fetchrow_calls = 0
        self.fetch_calls = 0

    async def fetchrow(self, query, *args):
        if "from autonomy_executions" in query:
            return None
        self.fetchrow_calls += 1
        if self.fetchrow_calls == 1:
            return {"id": "user-1", "conversation_id": "conversation-1", "created_at": NOW - timedelta(hours=3)}
        return {"created_at": NOW - timedelta(hours=1)}

    async def fetch(self, query, *args):
        if "from autonomy_executions" in query:
            return []
        self.fetch_calls += 1
        return [
            {"id": "proactive-1", "created_at": NOW - timedelta(hours=2)},
            {"id": "proactive-2", "created_at": NOW - timedelta(hours=1, minutes=30)},
        ]


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.acquire_count = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquire_count += 1
        yield self.connection


class AutonomyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_persona_settings_are_validated_and_default_safe(self):
        settings = Settings(_env_file=None)
        self.assertFalse(settings.proactive_enabled)
        self.assertEqual(settings.proactive_cooldown_seconds, 1800)
        self.assertTrue(settings.proactive_quiet_hours_enabled)
        self.assertEqual((settings.proactive_quiet_start, settings.proactive_quiet_end), ("23:00", "07:00"))
        with self.assertRaises(ValueError):
            Settings(_env_file=None, proactive_cooldown_seconds=20)
        with self.assertRaises(ValueError):
            Settings(_env_file=None, proactive_quiet_start="7pm")
        with self.assertRaises(ValueError):
            Settings(_env_file=None, proactive_quiet_start="08:00", proactive_quiet_end="08:00")

    def test_quiet_hours_boundaries_timezone_and_dst(self):
        settings = Settings(_env_file=None, proactive_quiet_hours_enabled=True,
                            proactive_quiet_start="23:00", proactive_quiet_end="07:00",
                            diana_timezone="Asia/Seoul")
        self.assertFalse(is_quiet_time(settings, datetime(2026, 9, 23, 13, 59, 59, tzinfo=timezone.utc)))
        self.assertTrue(is_quiet_time(settings, datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)))
        self.assertTrue(is_quiet_time(settings, datetime(2026, 9, 23, 21, 59, 59, tzinfo=timezone.utc)))
        self.assertFalse(is_quiet_time(settings, datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc)))
        dst = Settings(_env_file=None, proactive_quiet_start="01:00", proactive_quiet_end="03:00",
                       diana_timezone="America/New_York")
        # 2026-11-01 06:30 UTC is 01:30 during the repeated DST hour.
        self.assertTrue(is_quiet_time(dst, datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)))

    def test_ignored_streak_extends_cooldown_then_hard_suppresses(self):
        self.assertEqual(effective_cooldown_seconds(1800, 0), 1800)
        self.assertEqual(effective_cooldown_seconds(1800, 1), 3600)
        self.assertEqual(effective_cooldown_seconds(1800, 2), 7200)
        self.assertIsNone(effective_cooldown_seconds(1800, 3))

    async def test_durable_state_uses_latest_user_conversation_and_proactive_turns(self):
        pool = FakePool()
        state = await load_durable_autonomy_state(pool)
        self.assertEqual(state.conversation_id, "conversation-1")
        self.assertEqual(state.latest_user_message_id, "user-1")
        self.assertEqual(state.latest_user_activity_at, NOW - timedelta(hours=3))
        self.assertEqual(state.last_proactive_at, NOW - timedelta(hours=1))
        self.assertEqual(state.ignored_streak, 2)
        self.assertEqual(pool.acquire_count, 1)

    async def test_off_startup_grace_and_quiet_hours_never_query_or_call_provider(self):
        pool = FakePool()
        provider_calls = 0

        async def executor(**_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("disabled/gated cycle must not execute")

        settings = Settings(_env_file=None, persona_id="persona-a", proactive_enabled=False,
                            proactive_quiet_hours_enabled=False)
        runtime = AutonomyRuntimeState(started_at=NOW - timedelta(days=1))
        result = await run_autonomy_cycle(pool=pool, settings=settings, identity_prompt="test", now=NOW,
                                          runtime=runtime, executor=executor)
        self.assertEqual(result.reason, "disabled")
        settings.proactive_enabled = True
        runtime.started_at = NOW - timedelta(seconds=STARTUP_GRACE_SECONDS - 1)
        result = await run_autonomy_cycle(pool=pool, settings=settings, identity_prompt="test", now=NOW,
                                          runtime=runtime, executor=executor)
        self.assertEqual(result.reason, "startup_grace")
        settings.proactive_quiet_hours_enabled = True
        settings.proactive_quiet_start = "20:00"
        settings.proactive_quiet_end = "22:00"
        runtime.started_at = NOW - timedelta(days=1)
        result = await run_autonomy_cycle(pool=pool, settings=settings, identity_prompt="test", now=NOW,
                                          runtime=runtime, executor=executor)
        self.assertEqual(result.reason, "quiet_hours")
        self.assertEqual(pool.acquire_count, 0)
        self.assertEqual(provider_calls, 0)

    async def test_same_intention_is_suppressed_for_the_same_durable_user_activity(self):
        from uuid import uuid4

        intention = SimpleNamespace(intention_key="CHECK_IN:curiosity")
        decision = SimpleNamespace(action_class=ActionClass.ACT)
        durable = DurableAutonomyState(
            conversation_id="conversation-a", latest_user_message_id="user-a",
            latest_user_activity_at=NOW - timedelta(hours=3),
            last_proactive_at=None, ignored_streak=0,
        )
        settings = Settings(_env_file=None, persona_id="persona-a", proactive_enabled=True,
                            proactive_quiet_hours_enabled=False)
        runtime = AutonomyRuntimeState(started_at=NOW - timedelta(minutes=5))
        result = ProactiveExecutionResult(
            turn_id=uuid4(), conversation_id=uuid4(), message_id=uuid4(),
            intention_key=intention.intention_key, status="complete", created_at=NOW,
        )
        executor_calls = 0

        async def executor(**_kwargs):
            nonlocal executor_calls
            executor_calls += 1
            return result

        with patch("app.services.mindcore.autonomy_runtime.load_durable_autonomy_state",
                   new=AsyncMock(return_value=durable)), \
             patch("app.services.mindcore.autonomy_runtime.get_trigger_snapshot",
                   new=AsyncMock(return_value=(object(), object(), object()))), \
             patch("app.services.mindcore.autonomy_runtime.decide_autonomy", return_value=decision), \
             patch("app.services.mindcore.autonomy_runtime.derive_autonomy_intention", return_value=intention), \
             patch("app.services.mindcore.autonomy_runtime.get_execution_gate", new=AsyncMock(return_value=(False, None))):
            first = await run_autonomy_cycle(
                pool=object(), settings=settings, identity_prompt="test", now=NOW,
                runtime=runtime, executor=executor,
            )
            second = await run_autonomy_cycle(
                pool=object(), settings=settings, identity_prompt="test", now=NOW + timedelta(seconds=1),
                runtime=runtime, executor=executor,
            )
        self.assertEqual(first.status, "executed")
        self.assertEqual(runtime.last_successful_intention_at, NOW)
        self.assertEqual(second.reason, "same_intention")
        self.assertEqual(executor_calls, 1)

    async def test_m5_defer_or_do_not_act_never_reaches_m6_or_m7_executor(self):
        durable = DurableAutonomyState(
            conversation_id="conversation-a", latest_user_message_id="user-a",
            latest_user_activity_at=NOW - timedelta(hours=3),
            last_proactive_at=None, ignored_streak=0,
        )
        settings = Settings(_env_file=None, persona_id="persona-a", proactive_enabled=True,
                            proactive_quiet_hours_enabled=False)
        runtime = AutonomyRuntimeState(started_at=NOW - timedelta(minutes=5))
        executor_calls = 0

        async def executor(**_kwargs):
            nonlocal executor_calls
            executor_calls += 1
            raise AssertionError("M5 non-ACT must not execute M7")

        with patch("app.services.mindcore.autonomy_runtime.load_durable_autonomy_state",
                   new=AsyncMock(return_value=durable)), \
             patch("app.services.mindcore.autonomy_runtime.get_trigger_snapshot",
                   new=AsyncMock(return_value=(object(), object(), object()))), \
             patch("app.services.mindcore.autonomy_runtime.derive_autonomy_intention") as derive:
            for action in (ActionClass.DEFER, ActionClass.DO_NOT_ACT):
                with patch("app.services.mindcore.autonomy_runtime.decide_autonomy",
                           return_value=SimpleNamespace(action_class=action)):
                    result = await run_autonomy_cycle(
                        pool=object(), settings=settings, identity_prompt="test", now=NOW,
                        runtime=runtime, executor=executor,
                    )
                self.assertEqual(result.status, "evaluated")
                derive.assert_not_called()
        self.assertEqual(executor_calls, 0)


STARTUP_GRACE_SECONDS = 120

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import libsql

from app.database.turso import TursoConnection
from app.models.temporal_context import ActivityPoint, TemporalInputs
from app.services.mindcore.temporal_context import (
    DAYPART_BOUNDARIES,
    TEMPORAL_ACTIVITY_READ_LIMIT,
    compute_temporal_context,
    get_temporal_context,
)
from app.services.mindcore.trigger_context import get_trigger_snapshot


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")


def point(actor: str, age_seconds: int, source_ref: str | None = None) -> ActivityPoint:
    return ActivityPoint(
        actor=actor,  # type: ignore[arg-type]
        occurred_at=NOW - timedelta(seconds=age_seconds),
        source_ref=source_ref or f"{actor}-{age_seconds}",
        provenance="turn_context" if actor == "system" else "message_role",
    )


class TemporalProjectionTests(unittest.TestCase):
    def test_same_inputs_and_now_are_deterministic_and_restart_stable(self) -> None:
        inputs = TemporalInputs(activity=(point("user", 300), point("persona", 120)))
        first = compute_temporal_context(inputs, timezone_name="Asia/Seoul", now=NOW)
        second = compute_temporal_context(inputs, timezone_name="Asia/Seoul", now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first.last_user_activity_at, NOW - timedelta(seconds=300))
        self.assertEqual(first.last_persona_activity_at, NOW - timedelta(seconds=120))

    def test_timezone_conversion_and_daypart_boundaries(self) -> None:
        expected = ["late_night", "morning", "afternoon", "evening", "night"]
        boundary_hours = [0, *DAYPART_BOUNDARIES]
        for hour, daypart in zip(boundary_hours, expected):
            instant = datetime(2026, 9, 23, hour, tzinfo=timezone(timedelta(hours=9)))
            context = compute_temporal_context(TemporalInputs(), timezone_name="Asia/Seoul", now=instant)
            self.assertEqual(context.local_now.hour, hour)
            self.assertEqual(context.local_date.isoformat(), "2026-09-23")
            self.assertEqual(context.daypart, daypart)

    def test_latest_user_persona_system_and_conversation_activity(self) -> None:
        inputs = TemporalInputs(activity=(
            point("user", 3600, "u1"),
            point("persona", 1800, "p1"),
            point("user", 900, "u2"),
            point("system", 300, "s1"),
        ), user_has_ever_spoken=True, persona_has_ever_spoken=True)
        context = compute_temporal_context(inputs, timezone_name="UTC", now=NOW)
        self.assertEqual(context.last_user_activity_source_ref, "u2")
        self.assertEqual(context.last_persona_activity_source_ref, "p1")
        self.assertEqual(context.last_system_activity_source_ref, "s1")
        self.assertEqual(context.last_conversation_activity_at, NOW - timedelta(seconds=300))
        self.assertTrue(context.conversation_has_activity)
        self.assertTrue(context.user_has_ever_spoken)
        self.assertTrue(context.persona_has_ever_spoken)

    def test_idle_level_boundaries_and_monotonic_pressure(self) -> None:
        levels = [
            (119, "active"), (120, "recent"), (899, "recent"),
            (900, "idle"), (7199, "idle"), (7200, "long_idle"),
        ]
        pressures = []
        for age_seconds, level in levels:
            context = compute_temporal_context(
                TemporalInputs(activity=(point("user", age_seconds),)),
                timezone_name="UTC", now=NOW,
            )
            self.assertEqual(context.idle_level, level)
            pressures.append(context.idle_pressure)
        self.assertEqual(pressures, sorted(pressures))
        self.assertEqual(pressures[0], 0.0)
        self.assertGreater(pressures[-1], 0.0)

    def test_idle_pressure_clamps_at_configured_horizon(self) -> None:
        for age_seconds in (24 * 60 * 60, 4 * 24 * 60 * 60):
            old = point("user", age_seconds)
            context = compute_temporal_context(TemporalInputs(activity=(old,)), timezone_name="UTC", now=NOW)
            self.assertEqual(context.idle_pressure, 1.0)

    def test_empty_conversation_has_safe_zero_idle_semantics(self) -> None:
        context = compute_temporal_context(TemporalInputs(), timezone_name="UTC", now=NOW)
        self.assertFalse(context.conversation_has_activity)
        self.assertEqual(context.idle_duration_seconds, 0)
        self.assertEqual(context.idle_pressure, 0)
        self.assertEqual(context.idle_level, "active")

    def test_future_activity_has_zero_age_and_cannot_create_negative_idle(self) -> None:
        future = ActivityPoint("user", NOW + timedelta(days=4), "future-message", "message_role")
        context = compute_temporal_context(TemporalInputs(activity=(future,)), timezone_name="UTC", now=NOW)
        self.assertEqual(context.seconds_since_user_activity, 0)
        self.assertEqual(context.idle_duration_seconds, 0)
        self.assertEqual(context.idle_pressure, 0)
        self.assertIn("future_activity_timestamp_clamped_for_age", context.data_warnings)

    def test_naive_now_and_activity_are_explicitly_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            compute_temporal_context(TemporalInputs(), timezone_name="UTC", now=NOW.replace(tzinfo=None))
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            ActivityPoint("user", NOW.replace(tzinfo=None), "legacy", "message_role")


class _ReadOnlyConnection:
    def __init__(self, connection: TursoConnection) -> None:
        self.connection = connection
        self.reads: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args):
        self.reads.append((query, args))
        return await self.connection.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        self.reads.append((query, args))
        return await self.connection.fetchrow(query, *args)

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("temporal context must be read-only")

    def transaction(self):
        raise AssertionError("temporal context must not open a transaction")


class _ReadOnlyPool:
    def __init__(self, connection: _ReadOnlyConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class TemporalActivityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TursoConnection(self.raw)
        for statement in BASELINE_SQL.split(";"):
            if statement.strip():
                await self.connection.execute(statement)
        self.readonly = _ReadOnlyConnection(self.connection)
        self.pool = _ReadOnlyPool(self.readonly)
        await self.connection.execute(
            "insert into conversations(conversation_id,source_device,started_at) values($1,$2,$3)",
            "conv-a", "test", NOW - timedelta(hours=2),
        )
        await self.connection.execute(
            """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
               values($1,$2,$3,$4,$5,$6,$7)""",
            "u1", "conv-a", 1, "user", "body not selected", "test", NOW - timedelta(hours=1),
        )
        await self.connection.execute(
            """insert into messages(id,conversation_id,sequence,role,content,source_device,created_at)
               values($1,$2,$3,$4,$5,$6,$7)""",
            "p1", "conv-a", 2, "diana", "body not selected", "test", NOW - timedelta(minutes=30),
        )
        await self.connection.execute(
            """insert into chat_turns(
                 turn_id,conversation_id,user_message_id,status,created_at,updated_at,
                 initiator_actor,trigger_type,input_source
               ) values($1,$2,$3,'complete',$4,$4,'user','user_message','text')""",
            "turn-user", "conv-a", "u1", NOW - timedelta(hours=1),
        )
        await self.connection.execute(
            """insert into chat_turns(
                 turn_id,conversation_id,assistant_message_id,status,created_at,updated_at,
                 initiator_actor,trigger_type,input_source
               ) values($1,$2,$3,'complete',$4,$4,'persona','autonomy_decision','internal')""",
            "turn-persona", "conv-a", "p1", NOW - timedelta(minutes=30),
        )
        await self.connection.execute(
            """insert into chat_turns(
                 turn_id,conversation_id,status,created_at,updated_at,
                 initiator_actor,trigger_type,input_source
               ) values($1,$2,'complete',$3,$3,'system','system_event','internal')""",
            "turn-system", "conv-a", NOW - timedelta(minutes=10),
        )

    async def asyncTearDown(self) -> None:
        self.raw.close()

    async def test_v23_turn_context_and_legacy_messages_are_loaded_bounded_read_only(self) -> None:
        first = await get_temporal_context(self.pool, timezone_name="Asia/Seoul", now=NOW, conversation_id="conv-a")
        first_queries = list(self.readonly.reads)
        second = await get_temporal_context(self.pool, timezone_name="Asia/Seoul", now=NOW, conversation_id="conv-a")
        self.assertEqual(first, second)
        self.assertEqual(len(first_queries), 3)
        self.assertEqual(len(self.readonly.reads), 6)
        self.assertEqual(first.last_user_activity_source_ref, "u1")
        self.assertEqual(first.last_persona_activity_source_ref, "p1")
        self.assertEqual(first.last_system_activity_source_ref, "turn-system")
        self.assertEqual(first.idle_duration_seconds, 600)
        self.assertTrue(first.user_has_ever_spoken)
        self.assertTrue(first.persona_has_ever_spoken)
        self.assertTrue(all("content" not in query.casefold() for query, _ in first_queries))
        bounded = [args for query, args in first_queries if "limit" in query.casefold()]
        self.assertEqual(bounded, [("conv-a", TEMPORAL_ACTIVITY_READ_LIMIT)] * 2)

    async def test_legacy_role_only_messages_supply_activity_without_turn_rows(self) -> None:
        await self.connection.execute("delete from chat_turns")
        result = await get_temporal_context(self.pool, timezone_name="UTC", now=NOW, conversation_id="conv-a")
        self.assertEqual(result.last_user_activity_source_ref, "u1")
        self.assertEqual(result.last_persona_activity_source_ref, "p1")
        self.assertTrue(result.user_has_ever_spoken)
        self.assertTrue(result.persona_has_ever_spoken)

    async def test_user_only_conversation_has_no_persona_activity(self) -> None:
        await self.connection.execute("delete from messages where id=$1", "p1")
        await self.connection.execute("delete from chat_turns where turn_id in ($1,$2)", "turn-persona", "turn-system")
        result = await get_temporal_context(self.pool, timezone_name="UTC", now=NOW, conversation_id="conv-a")
        self.assertTrue(result.user_has_ever_spoken)
        self.assertFalse(result.persona_has_ever_spoken)
        self.assertIsNotNone(result.last_user_activity_at)
        self.assertIsNone(result.last_persona_activity_at)
        self.assertIsNone(result.last_system_activity_at)

    async def test_combined_temporal_m2_trigger_read_is_idempotent_and_has_no_writes(self) -> None:
        await self.connection.execute(
            "insert into diana_needs(need_key,value,baseline,updated_at,last_triggered_at) values($1,$2,$3,$4,$5)",
            "curiosity", .9, .4, NOW - timedelta(hours=2), NOW - timedelta(hours=1),
        )
        await self.connection.execute(
            """insert into diana_need_events(
                 id,need_key,delta,before_value,after_value,reason,source_type,source_id,
                 conversation_id,fingerprint,created_at
               ) values($1,$2,$3,$4,$5,$6,$7,$8,null,$9,$10)""",
            "event-1", "curiosity", .2, .7, .9, "fixture", "test", "source-1",
            "fingerprint-1", NOW - timedelta(hours=1),
        )
        await self.connection.execute(
            """insert into diana_goals(
                 id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,
                 source_type,source_id,created_at,updated_at,expires_at
               ) values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
            "goal-id", "learn:stars", "short_term", "fixture summary", "curiosity", .8,
            "active", 0, .9, "test", "goal-source", NOW - timedelta(days=20),
            NOW - timedelta(days=10), NOW + timedelta(days=1),
        )
        before = (
            await self.connection.fetchval("select count(*) from messages"),
            await self.connection.fetchval("select count(*) from chat_turns"),
            await self.connection.fetchval("select count(*) from diana_needs"),
            await self.connection.fetchval("select count(*) from diana_need_events"),
            await self.connection.fetchval("select count(*) from diana_goals"),
            await self.connection.fetchval("select value from schema_metadata where key=$1", "turso_baseline_version"),
        )
        first = await get_trigger_snapshot(
            self.pool, timezone_name="Asia/Seoul", now=NOW, conversation_id="conv-a"
        )
        self.assertEqual(len(self.readonly.reads), 6)
        second = await get_trigger_snapshot(
            self.pool, timezone_name="Asia/Seoul", now=NOW, conversation_id="conv-a"
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.readonly.reads), 12)
        trigger_types = {item.trigger_type.value for item in first[2].triggers}
        self.assertIn("need_activation", trigger_types)
        self.assertIn("goal_staleness", trigger_types)
        self.assertIn("goal_deadline", trigger_types)
        self.assertIn("idle_time", trigger_types)
        after = (
            await self.connection.fetchval("select count(*) from messages"),
            await self.connection.fetchval("select count(*) from chat_turns"),
            await self.connection.fetchval("select count(*) from diana_needs"),
            await self.connection.fetchval("select count(*) from diana_need_events"),
            await self.connection.fetchval("select count(*) from diana_goals"),
            await self.connection.fetchval("select value from schema_metadata where key=$1", "turso_baseline_version"),
        )
        self.assertEqual(after, before)

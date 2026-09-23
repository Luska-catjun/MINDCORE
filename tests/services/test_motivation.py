from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import libsql

from app.database.turso import TursoConnection
from app.models.motivation import MotivationalSnapshot
from app.services.mindcore.motivation import (
    DOMINANT_ACTIVATION_THRESHOLD,
    build_motivational_snapshot,
    get_motivational_snapshot,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = (ROOT / "db" / "turso" / "baseline_v1.sql").read_text(encoding="utf-8")


def need_row(
    key: str = "curiosity",
    *,
    value: float = 0.9,
    baseline: float = 0.4,
    updated_at: datetime = NOW,
    last_triggered_at: datetime | None = NOW,
) -> dict[str, object]:
    return {
        "need_key": key,
        "value": value,
        "baseline": baseline,
        "updated_at": updated_at.isoformat(),
        "last_triggered_at": last_triggered_at.isoformat() if last_triggered_at else None,
    }


def event_row(
    event_id: str,
    *,
    key: str = "curiosity",
    created_at: datetime = NOW,
    before: float = 0.4,
    after: float = 0.5,
) -> dict[str, object]:
    return {
        "id": event_id,
        "need_key": key,
        "before_value": before,
        "after_value": after,
        "source_type": "test_signal",
        "source_id": event_id,
        "created_at": created_at.isoformat(),
    }


def goal_row(
    key: str,
    *,
    origin_need: str = "curiosity",
    priority: float = 0.8,
    confidence: float = 0.8,
    progress: float = 0.0,
    status: str = "active",
    created_at: datetime = NOW - timedelta(days=1),
    updated_at: datetime = NOW,
    expires_at: datetime | None = NOW + timedelta(days=3),
) -> dict[str, object]:
    return {
        "id": f"id-{key}",
        "goal_key": key,
        "origin_need": origin_need,
        "priority": priority,
        "status": status,
        "progress": progress,
        "confidence": confidence,
        "source_type": "test_source",
        "source_id": f"source-{key}",
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


def build(
    *,
    needs=(),
    events=(),
    goals=(),
    now: datetime = NOW,
) -> MotivationalSnapshot:
    return build_motivational_snapshot(
        need_rows=needs,
        need_event_rows=events,
        goal_rows=goals,
        now=now,
    )


class _ReadOnlyConnection:
    def __init__(self, connection: TursoConnection) -> None:
        self.connection = connection
        self.fetch_count = 0

    async def fetch(self, query: str, *args):
        self.fetch_count += 1
        return await self.connection.fetch(query, *args)

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("MotivationalSnapshot reads must not write durable state")

    def transaction(self):
        raise AssertionError("MotivationalSnapshot reads must not open transactions")


class _ReadOnlyPool:
    def __init__(self, connection: _ReadOnlyConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class MotivationalProjectionUnitTests(unittest.TestCase):
    def test_same_inputs_and_fixed_now_produce_identical_immutable_snapshot(self) -> None:
        args = {
            "needs": [need_row()],
            "events": [event_row("e1")],
            "goals": [goal_row("learn")],
        }
        first = build(**args, now=NOW)
        second = build(**args, now=NOW)
        self.assertEqual(first, second)
        with self.assertRaises((AttributeError, TypeError)):
            first.needs += ()

    def test_no_data_returns_a_safe_empty_snapshot(self) -> None:
        snapshot = build()
        self.assertEqual(snapshot, MotivationalSnapshot(generated_at=NOW))
        self.assertEqual(snapshot.needs, ())
        self.assertEqual(snapshot.goals, ())
        self.assertEqual(snapshot.dominant_need_keys, ())
        self.assertEqual(snapshot.dominant_goal_keys, ())

    def test_all_scores_are_deterministically_clamped_and_warned(self) -> None:
        snapshot = build(
            needs=[need_row(value=3.0, baseline=2.0)],
            goals=[goal_row("clamped", priority=2.0, confidence=-1.0, progress=-4.0)],
        )
        need = snapshot.needs[0]
        goal = snapshot.goals[0]
        self.assertEqual((need.baseline, need.effective, need.activation), (1.0, 1.0, 0.0))
        self.assertEqual((goal.priority, goal.importance), (1.0, 1.0))
        self.assertIn("need_baseline_clamped", snapshot.data_warnings)
        self.assertIn("need_value_clamped", snapshot.data_warnings)
        self.assertIn("goal_priority_clamped", snapshot.data_warnings)
        self.assertIn("goal_confidence_clamped", snapshot.data_warnings)
        self.assertIn("goal_progress_clamped", snapshot.data_warnings)

    def test_invalid_goal_rows_are_omitted_with_content_free_warning(self) -> None:
        malformed = goal_row("potentially-user-derived-key", origin_need="")
        snapshot = build(goals=[malformed])
        self.assertEqual(snapshot.goals, ())
        self.assertIn("goal_origin_need_missing", snapshot.data_warnings)
        self.assertNotIn("potentially-user-derived-key", " ".join(snapshot.data_warnings))

    def test_unknown_need_key_is_kept_safe_and_reported_without_echoing_key(self) -> None:
        row = {**need_row(key="unregistered:possible-user-value"), "baseline": None}
        snapshot = build(needs=[row])
        self.assertEqual(snapshot.needs[0].baseline, 0.0)
        self.assertIn("unknown_need_key", snapshot.data_warnings)
        self.assertNotIn("unregistered:possible-user-value", " ".join(snapshot.data_warnings))

    def test_recent_need_event_increases_projected_activation(self) -> None:
        stale = need_row(updated_at=NOW - timedelta(hours=36))
        quiet = build(needs=[stale])
        recently_triggered = build(
            needs=[stale],
            events=[event_row("recent", created_at=NOW)],
        )
        self.assertGreater(recently_triggered.needs[0].activation, quiet.needs[0].activation)

    def test_old_events_lose_persistence_and_need_pressure_decays(self) -> None:
        stale = need_row(updated_at=NOW - timedelta(hours=36))
        old = build(
            needs=[stale],
            events=[event_row("old", created_at=NOW - timedelta(days=60))],
        ).needs[0]
        recent = build(
            needs=[stale],
            events=[event_row("recent", created_at=NOW)],
        ).needs[0]
        self.assertEqual(old.persistence, 0.0)
        self.assertLess(old.effective, recent.effective)
        self.assertLess(old.activation, recent.activation)

    def test_repeated_persistent_need_events_slow_derived_decay(self) -> None:
        stale = need_row(updated_at=NOW - timedelta(hours=36))
        transient = build(needs=[stale], events=[event_row("one")]).needs[0]
        persistent = build(
            needs=[stale],
            events=[event_row(f"e{index}") for index in range(5)],
        ).needs[0]
        self.assertGreater(persistent.persistence, transient.persistence)
        self.assertGreater(persistent.effective, transient.effective)

    def test_high_baseline_changes_effective_value_and_activation_normalization(self) -> None:
        high = build(needs=[need_row(value=0.9, baseline=0.8, updated_at=NOW - timedelta(hours=12))]).needs[0]
        low = build(needs=[need_row(value=0.9, baseline=0.2, updated_at=NOW - timedelta(hours=12))]).needs[0]
        self.assertGreater(high.effective, low.effective)
        self.assertGreater(high.activation, 0.0)

    def test_goal_origin_need_activation_contributes_to_goal_activation(self) -> None:
        active_goal = goal_row("origin-linked", priority=0.1, confidence=0.1, expires_at=None)
        low = build(needs=[need_row(value=0.4)], goals=[active_goal]).goals[0]
        high = build(needs=[need_row(value=0.95)], goals=[active_goal]).goals[0]
        self.assertGreater(high.activation, low.activation)
        self.assertIn("origin-linked", high.evidence_refs[0].id)

    def test_goal_importance_and_time_urgency_are_independent(self) -> None:
        important = goal_row(
            "important-not-urgent", priority=0.95,
            created_at=NOW - timedelta(hours=1), expires_at=NOW + timedelta(days=20),
        )
        urgent = goal_row(
            "urgent-not-important", priority=0.1,
            created_at=NOW - timedelta(days=19), expires_at=NOW + timedelta(days=1),
        )
        snapshot = build(needs=[need_row()], goals=[urgent, important])
        by_key = {item.goal_key: item for item in snapshot.goals}
        self.assertGreater(by_key["important-not-urgent"].importance, by_key["urgent-not-important"].importance)
        self.assertGreater(by_key["urgent-not-important"].urgency, by_key["important-not-urgent"].urgency)

    def test_deterministic_tie_ordering_and_dominant_selection(self) -> None:
        needs = [need_row(key="understanding"), need_row(key="curiosity")]
        goals = [goal_row("z-goal"), goal_row("a-goal")]
        snapshot = build(needs=needs, goals=goals)
        self.assertEqual([item.key for item in snapshot.needs], ["curiosity", "understanding"])
        self.assertEqual([item.goal_key for item in snapshot.goals], ["a-goal", "z-goal"])
        self.assertEqual(snapshot.dominant_need_keys, ("curiosity", "understanding"))
        self.assertEqual(snapshot.dominant_goal_keys, ("a-goal", "z-goal"))
        self.assertGreaterEqual(snapshot.needs[0].activation, DOMINANT_ACTIVATION_THRESHOLD)

    def test_snapshot_requires_aware_now_but_normalizes_legacy_naive_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            build(now=datetime(2026, 9, 23, 12))
        snapshot = build(needs=[{
            **need_row(),
            "updated_at": "2026-09-23T12:00:00",
            "last_triggered_at": None,
        }])
        self.assertIn("naive_timestamp_normalized:need_updated_at", snapshot.data_warnings)
        self.assertIsNone(snapshot.needs[0].last_triggered_at)


class MotivationalSnapshotReadIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TursoConnection(self.raw)
        for statement in BASELINE_SQL.split(";"):
            if statement.strip():
                await self.connection.execute(statement)
        self.read_only_connection = _ReadOnlyConnection(self.connection)
        self.pool = _ReadOnlyPool(self.read_only_connection)

    async def asyncTearDown(self) -> None:
        self.raw.close()

    async def test_fixture_rows_project_without_mutation_and_repeat_idempotently(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.connection.execute(
                "insert into diana_needs(need_key,value,baseline,updated_at,last_triggered_at) values($1,$2,$3,$4,$5)",
                "curiosity", 0.9, 0.4, NOW - timedelta(hours=2), NOW - timedelta(hours=1),
            )
            await connection.connection.execute(
                """insert into diana_need_events(
                    id,need_key,delta,before_value,after_value,reason,source_type,source_id,
                    conversation_id,fingerprint,created_at
                ) values($1,$2,$3,$4,$5,$6,$7,$8,null,$9,$10)""",
                "event-1", "curiosity", 0.2, 0.7, 0.9, "fixture", "test", "source-1",
                "fingerprint-1", NOW - timedelta(hours=2),
            )
            await connection.connection.execute(
                """insert into diana_goals(
                    id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,
                    conversation_id,source_type,source_id,created_at,updated_at,expires_at
                ) values($1,$2,$3,$4,$5,$6,$7,$8,$9,null,$10,$11,$12,$13,$14)""",
                "goal-id", "learn:stars", "short_term", "fixture summary", "curiosity", .8,
                "active", 0, .9, "test", "goal-source", NOW - timedelta(days=1), NOW,
                NOW + timedelta(days=1),
            )
            before = (
                await connection.connection.fetchval("select count(*) from diana_needs"),
                await connection.connection.fetchval("select count(*) from diana_need_events"),
                await connection.connection.fetchval("select count(*) from diana_goals"),
                await connection.connection.fetchval(
                    "select value from schema_metadata where key='turso_baseline_version'"
                ),
            )

        first = await get_motivational_snapshot(self.pool, now=NOW)
        first_read_count = self.read_only_connection.fetch_count
        second = await get_motivational_snapshot(self.pool, now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first_read_count, 3)
        self.assertEqual(self.read_only_connection.fetch_count, 6)
        self.assertEqual(first.needs[0].key, "curiosity")
        self.assertEqual(first.needs[0].related_goal_keys, ("learn:stars",))
        self.assertEqual(first.needs[0].evidence_refs[0].id, "event-1")
        self.assertEqual(first.goals[0].goal_key, "learn:stars")
        self.assertEqual(first.goals[0].evidence_refs[0].source_id, "goal-source")

        after = (
            await self.connection.fetchval("select count(*) from diana_needs"),
            await self.connection.fetchval("select count(*) from diana_need_events"),
            await self.connection.fetchval("select count(*) from diana_goals"),
            await self.connection.fetchval(
                "select value from schema_metadata where key='turso_baseline_version'"
            ),
        )
        self.assertEqual(after, before)

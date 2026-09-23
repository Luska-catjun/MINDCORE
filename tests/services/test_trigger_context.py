from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.models.motivation import (
    GoalState,
    MotivationEvidenceRef,
    MotivationalSnapshot,
    NeedState,
)
from app.models.temporal_context import ActivityPoint, TemporalInputs
from app.services.mindcore.temporal_context import compute_temporal_context
from app.services.mindcore.trigger_context import (
    GOAL_DEADLINE_HORIZON_SECONDS,
    compute_trigger_snapshot,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def temporal(*, ages: tuple[int, ...] = (), actors: tuple[str, ...] = ("user",)):
    points = tuple(
        ActivityPoint(actor, NOW - timedelta(seconds=age), f"{actor}-{index}", "message_role")
        for index, (actor, age) in enumerate(zip(actors, ages))
    )
    return compute_temporal_context(
        TemporalInputs(
            activity=points,
            user_has_ever_spoken="user" in actors,
            persona_has_ever_spoken="persona" in actors,
        ),
        timezone_name="UTC",
        now=NOW,
    )


def need(
    key: str = "curiosity", *, activation: float = 0.8, persistence: float = 0.4,
    occurred_at: datetime | None = NOW,
) -> NeedState:
    refs = (MotivationEvidenceRef("need_event", f"event-{key}", occurred_at=occurred_at),) if occurred_at else ()
    return NeedState(
        key=key, baseline=0.4, effective=0.9, activation=activation, urgency=0.6,
        persistence=persistence, last_triggered_at=occurred_at, evidence_refs=refs,
    )


def goal(
    key: str = "goal-a", *, updated_at: datetime = NOW - timedelta(days=10),
    expires_at: datetime | None = NOW + timedelta(days=1), status: str = "active",
) -> GoalState:
    ref = MotivationEvidenceRef("goal_source", f"source-{key}", occurred_at=updated_at)
    return GoalState(
        goal_key=key, status=status, priority=0.8, importance=0.8, urgency=0.4,
        activation=0.7, origin_need="curiosity", created_at=NOW - timedelta(days=20),
        updated_at=updated_at, expires_at=expires_at, evidence_refs=(ref,),
    )


def motivation(*, needs=(), goals=()) -> MotivationalSnapshot:
    return MotivationalSnapshot(generated_at=NOW, needs=tuple(needs), goals=tuple(goals))


class TriggerProjectionTests(unittest.TestCase):
    def test_identical_inputs_produce_identical_trigger_snapshot(self) -> None:
        temporal_context = temporal(ages=(60,), actors=("user",))
        motivational = motivation(needs=(need(),), goals=(goal(),))
        self.assertEqual(
            compute_trigger_snapshot(temporal=temporal_context, motivation=motivational),
            compute_trigger_snapshot(temporal=temporal_context, motivation=motivational),
        )

    def test_recent_user_trigger_is_fresh_and_old_transient_trigger_expires(self) -> None:
        recent = compute_trigger_snapshot(temporal=temporal(ages=(30,)), motivation=motivation())
        old = compute_trigger_snapshot(temporal=temporal(ages=(3600,)), motivation=motivation())
        self.assertEqual(len([item for item in recent.triggers if item.trigger_type.value == "user_activity"]), 1)
        self.assertGreater(recent.triggers[0].freshness, 0.9)
        self.assertFalse(any(item.trigger_type.value == "user_activity" for item in old.triggers))

    def test_idle_trigger_reflects_temporal_idle_pressure(self) -> None:
        context = temporal(ages=(3600,))
        snapshot = compute_trigger_snapshot(temporal=context, motivation=motivation())
        idle = next(item for item in snapshot.triggers if item.trigger_type.value == "idle_time")
        self.assertEqual(idle.strength, context.idle_pressure)
        self.assertGreater(idle.persistence, 0)

    def test_need_activation_is_projected_from_m2_without_mutating_it(self) -> None:
        source = need(activation=0.72, persistence=0.5)
        trigger = compute_trigger_snapshot(
            temporal=temporal(), motivation=motivation(needs=(source,))
        ).triggers
        projected = next(item for item in trigger if item.trigger_type.value == "need_activation")
        self.assertEqual(projected.strength, source.activation)
        self.assertEqual(projected.persistence, source.persistence)
        self.assertEqual(projected.related_need_keys, ("curiosity",))

    def test_persistent_need_trigger_freshness_decays_slower(self) -> None:
        occurred_at = NOW - timedelta(days=1)
        transient = need("transient", persistence=0.0, occurred_at=occurred_at)
        persistent = need("persistent", persistence=1.0, occurred_at=occurred_at)
        snapshot = compute_trigger_snapshot(
            temporal=temporal(), motivation=motivation(needs=(transient, persistent))
        )
        freshness = {item.key: item.freshness for item in snapshot.triggers if item.trigger_type.value == "need_activation"}
        self.assertGreater(freshness["persistent"], freshness["transient"])

    def test_goal_staleness_and_deadline_signals_use_durable_goal_times(self) -> None:
        snapshot = compute_trigger_snapshot(
            temporal=temporal(), motivation=motivation(goals=(goal(),))
        )
        stale = next(item for item in snapshot.triggers if item.trigger_type.value == "goal_staleness")
        deadline = next(item for item in snapshot.triggers if item.trigger_type.value == "goal_deadline")
        self.assertGreater(stale.strength, 0.0)
        self.assertGreater(stale.persistence, 0.0)
        self.assertAlmostEqual(deadline.strength, 1.0 - 86400 / GOAL_DEADLINE_HORIZON_SECONDS)
        self.assertEqual(stale.source_refs, ("source-goal-a",))

    def test_far_deadline_and_terminal_goal_do_not_emit_deadline_trigger(self) -> None:
        far = goal("far", updated_at=NOW, expires_at=NOW + timedelta(days=30))
        terminal = goal("done", status="satisfied", expires_at=NOW + timedelta(hours=1))
        snapshot = compute_trigger_snapshot(
            temporal=temporal(), motivation=motivation(goals=(far, terminal))
        )
        self.assertFalse(any(item.trigger_type.value == "goal_deadline" for item in snapshot.triggers))

    def test_duplicate_semantic_need_triggers_are_coalesced_and_refs_preserved(self) -> None:
        first = need(occurred_at=NOW - timedelta(minutes=1))
        second = NeedState(
            key="curiosity", baseline=.4, effective=.8, activation=.5, urgency=.2,
            persistence=.2, last_triggered_at=NOW - timedelta(minutes=3),
            evidence_refs=(MotivationEvidenceRef("need_event", "event-extra", occurred_at=NOW - timedelta(minutes=3)),),
        )
        snapshot = compute_trigger_snapshot(
            temporal=temporal(), motivation=motivation(needs=(first, second))
        )
        selected = [item for item in snapshot.triggers if item.trigger_type.value == "need_activation"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].source_refs, ("event-curiosity", "event-extra"))

    def test_ordering_is_stable_and_never_combines_strengths(self) -> None:
        snapshot = compute_trigger_snapshot(
            temporal=temporal(ages=(1800,), actors=("user",)),
            motivation=motivation(needs=(need("z", activation=.7), need("a", activation=.7)), goals=(goal(),)),
        )
        keys = [(str(item.trigger_type), item.key) for item in snapshot.triggers]
        repeated = compute_trigger_snapshot(
            temporal=temporal(ages=(1800,), actors=("user",)),
            motivation=motivation(needs=(need("z", activation=.7), need("a", activation=.7)), goals=(goal(),)),
        )
        self.assertEqual(keys, [(str(item.trigger_type), item.key) for item in repeated.triggers])
        self.assertEqual(snapshot.strongest_trigger_keys, tuple(item.key for item in snapshot.triggers[:3]))
        self.assertTrue(all(0 <= item.strength <= 1 for item in snapshot.triggers))

    def test_no_activity_and_no_durable_motivation_returns_empty_snapshot(self) -> None:
        snapshot = compute_trigger_snapshot(temporal=temporal(ages=(), actors=()), motivation=motivation())
        self.assertEqual(snapshot.triggers, ())
        self.assertEqual(snapshot.strongest_trigger_types, ())
        self.assertEqual(snapshot.strongest_trigger_keys, ())
        self.assertFalse(snapshot.has_active_triggers)

    def test_future_timestamps_have_nonnegative_age_and_safe_freshness(self) -> None:
        future_context = compute_temporal_context(
            TemporalInputs(activity=(ActivityPoint("user", NOW + timedelta(days=2), "future", "message_role"),)),
            timezone_name="UTC", now=NOW,
        )
        snapshot = compute_trigger_snapshot(temporal=future_context, motivation=motivation())
        user = next(item for item in snapshot.triggers if item.trigger_type.value == "user_activity")
        self.assertEqual(user.age_seconds, 0)
        self.assertEqual(user.freshness, 1)
        self.assertGreaterEqual(user.age_seconds, 0)

    def test_mismatched_snapshot_times_are_rejected(self) -> None:
        other = MotivationalSnapshot(generated_at=NOW + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "share_generated_at"):
            compute_trigger_snapshot(temporal=temporal(), motivation=other)

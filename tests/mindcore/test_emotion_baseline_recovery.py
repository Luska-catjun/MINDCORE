from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.services.mindcore.attention import build_attention_snapshot
from app.services.mindcore.internal_state import (
    EMOTION_BASELINES,
    EMOTION_NEUTRAL_EPSILON,
    MOOD_DRIVE_BASELINES,
    _default_state,
    apply_candidate,
    apply_mood_drive_decay,
    apply_time_decay,
    build_state_context,
    evaluate_state,
    primary_emotion,
)


NOW = datetime(2026, 9, 2, 1, tzinfo=timezone.utc)


def activated(text: str) -> dict:
    candidate = evaluate_state(text)
    assert candidate is not None
    state = apply_candidate(_default_state(), candidate)
    state["updated_at"] = NOW
    return state


class EmotionBaselineRecoveryTests(unittest.TestCase):
    def test_short_medium_and_long_positive_recovery_are_time_based(self) -> None:
        state = activated("너 정말 귀엽고 대단해")
        baseline = EMOTION_BASELINES["joy"]
        initial = state["emotion_vector"]["joy"] - baseline
        short, _ = apply_time_decay(state, current_time=NOW + timedelta(minutes=1))
        medium, _ = apply_time_decay(state, current_time=NOW + timedelta(hours=2))
        long, _ = apply_time_decay(state, current_time=NOW + timedelta(hours=12))

        self.assertGreater(short["emotion_vector"]["joy"] - baseline, initial * .99)
        self.assertAlmostEqual(medium["emotion_vector"]["joy"] - baseline, initial * .5, places=6)
        self.assertLess(long["emotion_vector"]["joy"] - baseline, EMOTION_NEUTRAL_EPSILON)
        self.assertEqual(long["emotion"], "neutral")

    def test_mood_recovers_more_slowly_than_fast_emotion(self) -> None:
        state = activated("너 정말 귀엽고 대단해")
        fast, _ = apply_time_decay(state, current_time=NOW + timedelta(hours=12))
        slow, _ = apply_mood_drive_decay(fast, current_time=NOW + timedelta(hours=12))
        self.assertEqual(fast["emotion"], "neutral")
        self.assertGreater(
            slow["mood_valence"] - MOOD_DRIVE_BASELINES["mood_valence"],
            (state["mood_valence"] - MOOD_DRIVE_BASELINES["mood_valence"]) * .70,
        )

    def test_negative_recovery_and_baseline_crossing_are_safe(self) -> None:
        state = activated("그 소식이 너무 슬퍼")
        recovered, _ = apply_time_decay(state, current_time=NOW + timedelta(hours=24))
        self.assertLess(
            recovered["emotion_vector"]["sadness"] - EMOTION_BASELINES["sadness"],
            EMOTION_NEUTRAL_EPSILON,
        )
        below = _default_state()
        below["emotion_vector"]["joy"] = .05
        below["updated_at"] = NOW
        recovering, _ = apply_time_decay(below, current_time=NOW + timedelta(hours=12))
        self.assertGreaterEqual(recovering["emotion_vector"]["joy"], .05)
        self.assertLessEqual(recovering["emotion_vector"]["joy"], EMOTION_BASELINES["joy"])

    def test_no_trigger_turns_recover_monotonically_and_new_evidence_still_works(self) -> None:
        state = activated("너 정말 귀엽고 대단해")
        baseline = EMOTION_BASELINES["joy"]
        distances = []
        for hours in (1, 2, 4, 8, 12):
            decayed, _ = apply_time_decay(state, current_time=NOW + timedelta(hours=hours))
            distances.append(decayed["emotion_vector"]["joy"] - baseline)
        self.assertEqual(distances, sorted(distances, reverse=True))
        recovered, _ = apply_time_decay(state, current_time=NOW + timedelta(hours=12))
        renewed = apply_candidate(recovered, evaluate_state("좋은 소식이야, 신나!"))
        self.assertGreater(renewed["emotion_vector"]["excitement"], recovered["emotion_vector"]["excitement"])

    def test_repeated_meaningful_evidence_can_outweigh_recovery(self) -> None:
        state = activated("너 정말 귀엽고 대단해")
        decayed, _ = apply_time_decay(state, current_time=NOW + timedelta(minutes=5))
        reinforced = apply_candidate(decayed, evaluate_state("너 정말 귀엽고 대단해"))
        self.assertGreater(reinforced["emotion_vector"]["joy"], state["emotion_vector"]["joy"])

    def test_ordinary_courtesy_relationship_and_historical_attribution_do_not_make_current_emotion(self) -> None:
        self.assertIsNone(evaluate_state("고마워."))
        self.assertIsNone(evaluate_state("다이애나랑 이야기하는 게 좋아."))
        self.assertIsNotNone(evaluate_state("함께해줘서 고마워."))
        state = _default_state()
        self.assertIsNone(build_state_context(state, [{"emotion": "joy", "delta": .14, "cause_summary": "old"}]))
        attention = build_attention_snapshot(user_text="점심 먹었어.", internal_state=state)
        self.assertFalse(any(item.source_type == "emotion" for item in attention.items))
        self.assertEqual(primary_emotion(state["emotion_vector"]), ("neutral", 0.0))


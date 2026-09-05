from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.services.mindcore.attention import build_attention_snapshot
from app.services.mindcore.relationship import (
    RELATIONSHIP_BASELINE,
    RelationshipState,
    _state,
    apply_relationship_signal,
    build_relationship_context,
    evaluate_relationship_signal,
)


def baseline() -> RelationshipState:
    return RelationshipState(**RELATIONSHIP_BASELINE)


def repeat(text: str, count: int) -> RelationshipState:
    signal = evaluate_relationship_signal(text)
    assert signal is not None
    state = baseline()
    for _ in range(count):
        state, _ = apply_relationship_signal(state, signal)
    return state


class RelationshipCalibrationTests(unittest.TestCase):
    def test_conflict_recovers_with_a_sixty_day_half_life_and_context_uses_effective_value(self) -> None:
        anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for days, expected in ((0, .20), (60, .10), (120, .05), (180, .025)):
            state = _state({"familiarity": .1, "trust": .3, "affection": .15, "conflict": .20, "updated_at": anchor}, now=anchor + timedelta(days=days))
            self.assertAlmostEqual(state.conflict, expected, places=6)
        recovered = _state({"familiarity": .1, "trust": .3, "affection": .15, "conflict": .20, "updated_at": anchor}, now=anchor + timedelta(days=60))
        complaint = evaluate_relationship_signal("오늘 답변은 좀 별로다.")
        distrust = evaluate_relationship_signal("계속 약속을 안 지켜서 못 믿겠어.")
        assert complaint is not None and distrust is not None
        self.assertAlmostEqual(apply_relationship_signal(recovered, complaint)[0].conflict, .1045, places=6)
        self.assertAlmostEqual(apply_relationship_signal(recovered, distrust)[0].conflict, .1135, places=6)
        self.assertIsNone(build_relationship_context(_state({"familiarity": .1, "trust": .3, "affection": .15, "conflict": .20, "updated_at": anchor}, now=anchor + timedelta(days=60))))
    def test_36_turn_calibration_funnel_preserves_boundaries(self) -> None:
        positives = [
            "계속 기억해줘서 고마워. 덕분에 진짜 편하다.",
            "이건 믿고 맡길게.",
            "전에 말한 걸 아직 기억하고 있었네.",
            "오늘도 같이 하자.",
            "같이 해줘서 고마워. 덕분에 힘이 됐어.",
        ] * 2
        negatives = ["계속 약속을 안 지켜서 못 믿겠어."] * 6
        neutral = ["고마워.", "응.", "좋아.", "ㅋㅋ", "점심 먹었어.", "같이 게임하는 거 좋아.", "게임 너무 재밌다 ㅋㅋ", "오늘 학교 재밌었어."] * 2
        corpus = [*positives, *negatives, *neutral]
        signals = [evaluate_relationship_signal(text) for text in corpus]
        self.assertEqual(len(corpus), 32)
        self.assertEqual(sum(signal is not None for signal in signals), 16)
        self.assertEqual(sum(signal is not None and signal.kind == "explicit_distrust" for signal in signals), 6)
        self.assertEqual(sum(signal is None for signal in signals), 16)
        self.assertTrue(all(signal is None or any(signal.delta.values()) for signal in signals))

    def test_meaningful_positive_progression_is_slow_but_observable(self) -> None:
        one = repeat("같이 해줘서 고마워. 덕분에 진짜 편하다.", 1)
        ten = repeat("같이 해줘서 고마워. 덕분에 진짜 편하다.", 10)
        fifty = repeat("같이 해줘서 고마워. 덕분에 진짜 편하다.", 50)
        hundred = repeat("같이 해줘서 고마워. 덕분에 진짜 편하다.", 100)
        self.assertLess(one.trust - baseline().trust, .02)
        self.assertGreater(ten.trust - baseline().trust, .07)
        self.assertGreater(fifty.trust - baseline().trust, .30)
        self.assertGreater(hundred.trust - baseline().trust, .48)
        self.assertLess(hundred.trust, 1.0)

    def test_neutral_preference_and_emotion_only_text_do_not_change_relationship(self) -> None:
        neutral_texts = ("고마워.", "응.", "좋아.", "ㅋㅋ", "점심 먹었어.", "같이 게임하는 거 좋아.", "게임 너무 재밌다 ㅋㅋ")
        state = baseline()
        for _ in range(100):
            for text in neutral_texts:
                self.assertIsNone(evaluate_relationship_signal(text))
        self.assertEqual(state, baseline())

    def test_negative_progression_and_single_event_protection(self) -> None:
        complaint = evaluate_relationship_signal("오늘 답변은 좀 별로다.")
        assert complaint is not None
        after_one, delta = apply_relationship_signal(baseline(), complaint)
        self.assertLess(abs(delta["trust"]), .01)
        self.assertLess(after_one.conflict, .01)
        ten = repeat("계속 약속을 안 지켜서 못 믿겠어.", 10)
        fifty = repeat("계속 약속을 안 지켜서 못 믿겠어.", 50)
        self.assertLess(ten.trust, .27)
        self.assertLess(fifty.trust, .16)
        self.assertGreater(fifty.conflict, .50)

    def test_mixed_independent_evidence_is_bounded_and_not_volatile(self) -> None:
        state = baseline()
        for text in (
            "계속 기억해줘서 고마워. 덕분에 진짜 편하다.",
            "이건 믿고 맡길게.",
            "점심 먹었어.",
            "오늘 답변은 좀 별로다.",
            "오늘도 같이 하자.",
            "ㅋㅋ",
            "전에 말한 걸 아직 기억하고 있었네.",
            "계속 약속을 안 지켜서 못 믿겠어.",
        ):
            signal = evaluate_relationship_signal(text)
            if signal is not None:
                state, _ = apply_relationship_signal(state, signal)
        self.assertGreater(state.trust, .30)
        self.assertLess(state.conflict, .03)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in (state.familiarity, state.trust, state.affection, state.conflict)))

    def test_attention_stays_relational_only_for_relational_turns(self) -> None:
        state = {"familiarity": .4, "trust": .7, "affection": .6, "conflict": .0}
        ordinary = build_attention_snapshot(user_text="점심 먹었어.", relationship_state=state)
        relational = build_attention_snapshot(user_text="믿고 맡길게.", relationship_state=state)
        self.assertFalse(any(item.source_type == "relationship" for item in ordinary.items))
        self.assertTrue(any(item.source_type == "relationship" for item in relational.items))

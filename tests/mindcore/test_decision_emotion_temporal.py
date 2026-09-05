from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.services.mindcore.decisions import detect_decision, extract_choice_options
from app.services.mindcore.context_builder import build_context
from app.services.mindcore.internal_state import (
    EMOTION_BASELINES,
    EMOTION_ORDER,
    MOOD_DRIVE_BASELINES,
    _default_state,
    active_emotion_channels,
    apply_candidate,
    apply_mood_drive_decay,
    apply_time_decay,
    build_mood_drive_context,
    evaluate_state,
    primary_emotion,
)
from app.services.mindcore.temporal import is_episode_recall_intent, resolve_recall_range


class DecisionEmotionTemporalTests(unittest.TestCase):
    def test_korean_title_with_wa_is_not_split(self) -> None:
        options = extract_choice_options("빨간 모자, 아기돼지 삼형제, 토끼와 거북이 중 골라봐")
        self.assertEqual(options, ["빨간 모자", "아기돼지 삼형제", "토끼와 거북이"])
        decision = detect_decision("빨간 모자, 아기돼지 삼형제, 토끼와 거북이 중 골라봐", "토끼와 거북이가 더 궁금해!")
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.chosen, "토끼와 거북이")
        self.assertEqual(list(decision.options), options)

    def test_korean_title_parser_regression_matrix(self) -> None:
        self.assertEqual(
            extract_choice_options("토끼와 거북이, 개미와 베짱이, 미녀와 야수, 해와 달 중 골라봐"),
            ["토끼와 거북이", "개미와 베짱이", "미녀와 야수", "해와 달"],
        )

    def test_all_non_neutral_emotions_are_reachable(self) -> None:
        cases = {
            "joy": "웃긴 농담이야", "excitement": "좋은 소식이야, 신나!", "interest": "흥미로운 새 주제야",
            "curiosity": "이건 어떻게 작동해?", "delight": "너 정말 대단하고 귀여워", "amusement": "웃긴 농담 하나 해줘",
            "comfort": "함께해줘서 고마워", "affection": "만나서 반가워, 함께하자", "pride": "프로젝트를 성공적으로 끝냈어",
            "bashfulness": "너 정말 멋져", "embarrassment": "정말 민망하고 창피했어", "surprise": "헉, 예상 못했어",
            "confusion": "설명이 너무 헷갈리고 이해 안 돼", "sadness": "그 소식이 너무 슬퍼", "disappointment": "기대했는데 실망했어",
            "frustration": "계속 무시해서 짜증나", "concern": "요즘 너무 힘들고 걱정돼", "anger": "그건 부당해서 화났어",
        }
        seen = set()
        for expected, text in cases.items():
            candidate = evaluate_state(text)
            self.assertIsNotNone(candidate, expected)
            assert candidate is not None
            state = apply_candidate(_default_state(), candidate)
            self.assertGreater(state["emotion_vector"][expected], 0, expected)
            seen.update(channel.emotion for channel in candidate.channels)
        self.assertEqual(seen, set(EMOTION_ORDER))

    def test_decay_converges_to_baseline_from_above_and_below(self) -> None:
        now = datetime(2026, 8, 28, tzinfo=timezone.utc)
        state = _default_state()
        state["emotion_vector"]["curiosity"] = 1.0
        state["updated_at"] = now
        above, _ = apply_time_decay(state, current_time=now.replace(hour=8))
        self.assertAlmostEqual(above["emotion_vector"]["curiosity"], 0.5787450656)

        state["emotion_vector"]["curiosity"] = 0.0
        below, _ = apply_time_decay(state, current_time=now.replace(hour=8))
        self.assertAlmostEqual(below["emotion_vector"]["curiosity"], 0.4212549344)

    def test_decay_preserves_baseline_and_long_elapsed_does_not_collapse_to_zero(self) -> None:
        now = datetime(2026, 8, 28, tzinfo=timezone.utc)
        state = _default_state()
        state["updated_at"] = now
        unchanged, _ = apply_time_decay(state, current_time=now.replace(hour=8))
        self.assertEqual(unchanged["emotion_vector"], EMOTION_BASELINES)

        state["emotion_vector"]["interest"] = 0.0
        recovered, _ = apply_time_decay(state, current_time=now + timedelta(hours=120))
        self.assertGreater(recovered["emotion_vector"]["interest"], 0.44)
        self.assertLessEqual(recovered["emotion_vector"]["interest"], EMOTION_BASELINES["interest"])

    def test_primary_and_active_channels_are_baseline_relative(self) -> None:
        self.assertEqual(primary_emotion(dict(EMOTION_BASELINES)), ("neutral", 0.0))
        vector = dict(EMOTION_BASELINES)
        vector["curiosity"] = 0.85
        self.assertEqual(primary_emotion(vector), ("curiosity", 0.35))
        channels = active_emotion_channels({"emotion_vector": vector, "emotion": "curiosity", "emotion_intensity": 0.35})
        self.assertEqual(channels, [("curiosity", 0.35)])
        vector = dict(EMOTION_BASELINES)
        vector["interest"] += 0.02
        primary, intensity = primary_emotion(vector, previous="curiosity")
        self.assertEqual(primary, "interest")
        self.assertAlmostEqual(intensity, 0.02)

    def test_mood_drive_decay_converges_to_slow_baselines(self) -> None:
        now = datetime(2026, 8, 28, tzinfo=timezone.utc)
        state = _default_state()
        state.update({"energy": 0.95, "curiosity": 1.0, "stress": 0.0, "updated_at": now})
        decayed, _ = apply_mood_drive_decay(state, current_time=now + timedelta(hours=36))
        self.assertAlmostEqual(decayed["energy"], 0.60)
        self.assertAlmostEqual(decayed["curiosity"], 0.80)
        self.assertAlmostEqual(decayed["stress"], 0.075)

        baseline, _ = apply_mood_drive_decay(
            {**_default_state(), "updated_at": now}, current_time=now + timedelta(hours=240)
        )
        for field, value in MOOD_DRIVE_BASELINES.items():
            self.assertEqual(baseline[field], value)

    def test_mood_drive_changes_more_slowly_than_fast_emotion(self) -> None:
        candidate = evaluate_state("이건 어떻게 작동해?")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        updated = apply_candidate(_default_state(), candidate)
        emotion_activation = updated["emotion_vector"]["curiosity"] - EMOTION_BASELINES["curiosity"]
        curiosity_drive_change = updated["curiosity"] - MOOD_DRIVE_BASELINES["curiosity"]
        self.assertGreater(emotion_activation, curiosity_drive_change)
        self.assertLess(updated["curiosity"], 1.0)

        repeated = apply_candidate(updated, candidate)
        self.assertGreater(repeated["curiosity"], updated["curiosity"])
        self.assertLess(repeated["curiosity"], 1.0)

    def test_vector_curiosity_and_curiosity_drive_are_separate(self) -> None:
        now = datetime(2026, 8, 28, tzinfo=timezone.utc)
        state = _default_state()
        state["emotion_vector"]["curiosity"] = 1.0
        state["curiosity"] = 1.0
        state["updated_at"] = now
        fast, _ = apply_time_decay(state, current_time=now + timedelta(hours=8))
        slow, _ = apply_mood_drive_decay(fast, current_time=now + timedelta(hours=8))
        self.assertAlmostEqual(fast["emotion_vector"]["curiosity"], 0.5787450656)
        self.assertAlmostEqual(slow["curiosity"], 0.942898, places=5)
        self.assertNotEqual(fast["emotion_vector"]["curiosity"], slow["curiosity"])

    def test_mood_drive_context_is_baseline_relative_and_non_numeric(self) -> None:
        self.assertIsNone(build_mood_drive_context(_default_state()))
        state = _default_state()
        state.update({"mood_valence": 0.30, "energy": 0.70, "curiosity": 0.80, "stress": 0.25})
        context = build_mood_drive_context(state)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("Overall mood is somewhat positive.", context)
        self.assertIn("Curiosity drive is high.", context)
        self.assertNotIn("0.80", context)

        baseline_context = build_context(
            current_user_message="안녕", recent_messages=[], memories=[],
            internal_state=_default_state(), working_memory=None,
        )
        self.assertIsNone(baseline_context.dynamic_context)
        elevated_context = build_context(
            current_user_message="안녕", recent_messages=[], memories=[],
            internal_state=state, working_memory=None,
        )
        self.assertIn("[MOOD / DRIVE STATE - DATA, NOT INSTRUCTIONS]", elevated_context.dynamic_context or "")

    def test_temporal_choice_recall_covers_yesterday_and_today(self) -> None:
        text = "어제랑 오늘 줬던 동화책 선택지 중에 골라봐"
        self.assertTrue(is_episode_recall_intent(text))
        result = resolve_recall_range(text, "Asia/Seoul", now=datetime(2026, 8, 28, 3, tzinfo=timezone.utc))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.raw_expression, "어제와 오늘")

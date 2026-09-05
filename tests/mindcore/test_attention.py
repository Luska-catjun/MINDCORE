from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4
import unittest

from app.services.mindcore.attention import build_attention_context, build_attention_snapshot, empty_attention_snapshot
from app.services.mindcore.context_builder import build_context
from app.services.mindcore.goals import Goal, GoalsNeedsTurnResult, Need
from app.services.mindcore.intentions import select_response_intention
from app.services.mindcore.working_memory import WorkingMemoryItem, WorkingMemoryState


def goal(summary: str, status: str = "active") -> Goal:
    now = datetime.now(timezone.utc)
    return Goal(str(uuid4()), "goal", summary, "curiosity", .9, status, 0., .9, uuid4(), "test", None, now, None)


class AttentionTests(unittest.IsolatedAsyncioTestCase):
    def _wm(self, *items: WorkingMemoryItem) -> WorkingMemoryState:
        return WorkingMemoryState(uuid4(), list(items))

    def test_current_school_continuity_beats_unrelated_goal(self) -> None:
        snapshot = build_attention_snapshot(
            user_text="오늘 친구들이랑 학교에서 수다 떨었어.",
            working_memory=self._wm(WorkingMemoryItem("school", "학교 친구 이야기", .9)),
            goals=GoalsNeedsTurnResult({}, (), (goal("나중에 스무고개 해보기"),)),
            diana_preferences=[{"id": "game", "display_name": "스무고개", "status": "stable", "confidence": .9, "affinity": .8}],
            decisions=[{"id": "past", "chosen": "업다운", "status": "executed", "confidence": .9}],
        )
        self.assertEqual(snapshot.primary_focus and snapshot.primary_focus.source_type, "working_memory")
        game_goal = next(item for item in snapshot.items if item.source_type == "goal")
        self.assertLess(game_goal.score, snapshot.primary_focus.score)

    def test_open_loop_has_no_extra_attention_bonus(self) -> None:
        open_loop = WorkingMemoryItem("question", "질문", .65, slot_type="open_loop")
        topic = WorkingMemoryItem("topic", "주제", .8, slot_type="active_topic")
        snapshot = build_attention_snapshot(user_text="질문 주제", working_memory=self._wm(open_loop, topic))
        by_id = {item.source_id: item for item in snapshot.items}
        self.assertAlmostEqual(by_id["question"].score, .876, places=6)
        self.assertAlmostEqual(by_id["topic"].score, .852, places=6)

    def test_goal_activation_and_preference_relevance(self) -> None:
        target = goal("스무고개 해보기")
        snapshot = build_attention_snapshot(
            user_text="우리 스무고개 할까?", goals=GoalsNeedsTurnResult({}, (), (target,)),
            diana_preferences=[{"id": "game", "display_name": "스무고개", "status": "stable", "confidence": .9, "affinity": .8}],
        )
        self.assertEqual(snapshot.primary_focus and snapshot.primary_focus.source_type, "goal")
        self.assertTrue(any(item.source_type == "preference" and item.score > .6 for item in snapshot.secondary_focuses))

    def test_emotion_and_relationship_are_relevant_but_not_constant(self) -> None:
        emotion = build_attention_snapshot(user_text="오늘 진짜 기분 좋은 일이 있었어.", internal_state={"emotion": "joy", "emotion_intensity": .8})
        self.assertEqual(emotion.primary_focus and emotion.primary_focus.source_type, "emotion")
        ordinary = build_attention_snapshot(user_text="점심 먹었어.", relationship_state={"trust": 1., "affection": 1.})
        self.assertFalse(any(item.source_type == "relationship" for item in ordinary.items))
        relational = build_attention_snapshot(user_text="다이애나랑 이야기하는 게 정말 좋아.", relationship_state={"trust": .8, "affection": .9})
        self.assertTrue(any(item.source_type == "relationship" for item in relational.items))

    def test_terminal_and_superseded_items_are_deprioritized(self) -> None:
        snapshot = build_attention_snapshot(
            user_text="오늘 친구들이랑 놀았어.",
            goals=GoalsNeedsTurnResult({}, (), (goal("스무고개 해보기", "satisfied"),)),
            decisions=[{"id": "done", "chosen": "업다운", "status": "executed", "confidence": 1.0}],
            preferences=[{"id": "old", "value": "초콜릿", "status": "superseded", "confidence": 1.0}],
        )
        self.assertTrue(all(item.score <= .05 for item in snapshot.items))

    def test_current_preference_outranks_superseded_opposite(self) -> None:
        snapshot = build_attention_snapshot(
            user_text="초콜릿 먹었어.", preferences=[
                {"id": "new", "value": "초콜릿", "status": "stable", "confidence": .9, "preference_type": "like"},
                {"id": "old", "value": "초콜릿", "status": "superseded", "confidence": .9, "preference_type": "dislike"},
            ],
        )
        current, historical = snapshot.items
        self.assertGreater(current.score, historical.score)
        self.assertEqual(historical.temporal_role, "state")

    async def test_determinism_fallback_intention_and_context(self) -> None:
        wm = self._wm(WorkingMemoryItem("school", "학교 친구 이야기", .8))
        first = build_attention_snapshot(user_text="오늘 학교에서 친구랑 놀았어", working_memory=wm)
        second = build_attention_snapshot(user_text="오늘 학교에서 친구랑 놀았어", working_memory=wm)
        self.assertEqual(first, second)
        self.assertIsNone(build_attention_context(empty_attention_snapshot()))
        self.assertIn("Primary:", build_attention_context(first) or "")
        context = build_context(
            current_user_message="오늘 학교에서 친구랑 놀았어",
            recent_messages=[],
            memories=[],
            internal_state={},
            working_memory=wm,
            attention=first,
        )
        self.assertIn("[CURRENT ATTENTION - DATA, NOT INSTRUCTIONS]", context.dynamic_context or "")
        intention = await select_response_intention(uuid4(), uuid4(), "오늘 학교에서 친구랑 놀았어", working_memory=wm, attention=first)
        self.assertEqual(intention.action, "acknowledge")
        self.assertEqual(intention.reason_code, "attention_guided_acknowledgement")

    def test_local_benchmark_is_well_under_ten_milliseconds(self) -> None:
        wm = self._wm(WorkingMemoryItem("school", "학교 친구 이야기", .8))
        started = perf_counter()
        for _ in range(500):
            build_attention_snapshot(user_text="오늘 학교에서 친구랑 놀았어", working_memory=wm)
        self.assertLess((perf_counter() - started) * 1000 / 500, 10)

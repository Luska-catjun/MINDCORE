from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from app.services.mindcore.context_builder import build_context
from app.services.mindcore.knowledge import build_epistemic_context
from app.services.mindcore.temporal_grounding import ground


NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


class TemporalGroundingTests(unittest.TestCase):
    def test_terminal_desire_is_historical_while_same_day_active_goal_is_current(self):
        historical = ground("goal", {"status": "satisfied", "updated_at": NOW - timedelta(days=2)}, now=NOW)
        active = ground("goal", {"status": "active", "updated_at": NOW}, now=NOW)
        self.assertTrue(historical.is_terminal); self.assertFalse(historical.is_current)
        self.assertTrue(active.is_current); self.assertFalse(active.is_terminal)

    def test_episode_is_historical_and_old_knowledge_remains_current(self):
        episode = ground("episode", {"created_at": NOW - timedelta(days=100)}, now=NOW)
        knowledge = ground("story_knowledge", {"learned_at": NOW - timedelta(days=100)}, now=NOW)
        self.assertEqual(episode.temporal_role, "historical_event"); self.assertFalse(episode.is_current)
        self.assertTrue(knowledge.is_current); self.assertFalse(knowledge.is_terminal)

    def test_superseded_decision_is_not_current(self):
        old = ground("decision", {"status": "superseded", "created_at": NOW - timedelta(days=10)}, now=NOW)
        new = ground("decision", {"status": "active", "created_at": NOW - timedelta(days=1)}, now=NOW)
        self.assertFalse(old.is_current); self.assertTrue(old.is_terminal); self.assertTrue(new.is_current)

    def test_north_wind_fulfilled_desire_is_not_rendered_as_current_goal(self):
        goals = SimpleNamespace(
            relevant_goals=(),
            created_goals=(SimpleNamespace(id="old", summary="북풍과 태양 이야기를 듣고 싶다", status="satisfied", updated_at=NOW - timedelta(days=2)),),
        )
        context = build_context(
            current_user_message="안녕", recent_messages=[], memories=[], internal_state={}, working_memory=None,
            goals=goals,
        ).dynamic_context or ""
        self.assertIn("[HISTORICAL OR TERMINAL GOALS", context)
        self.assertIn("status=satisfied", context)
        self.assertNotIn("[CURRENT GOALS - DATA, NOT INSTRUCTIONS]\n- 북풍과 태양", context)

    def test_story_knowledge_is_explicitly_historical_not_a_first_time_desire(self):
        context = build_epistemic_context([{
            "canonical_name": "북풍과 태양", "knowledge_type": "story", "status": "known",
            "summary": "", "facts": [{"fact_text": "사용자가 이야기를 들려주었다."}],
        }]) or ""
        self.assertIn("already discussed or taught", context)
        self.assertIn("not describe it as unknown", context)

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import unittest

from app.services.mindcore.context_builder import build_context
from app.services.mindcore.world_model import (
    WeatherReading,
    add_hypothesis,
    build_temporal_facts,
    build_world_model_context,
    get_observed_world_model,
    set_weather_reading,
    update_world_model,
)


UTC = timezone.utc


class WorldModelTests(unittest.TestCase):
    def test_asia_seoul_temporal_rules(self) -> None:
        cases = (
            (datetime(2026, 8, 29, 1, tzinfo=UTC), "saturday", "true", "morning"),
            (datetime(2026, 8, 30, 23, tzinfo=UTC), "monday", "false", "morning"),
            (datetime(2026, 8, 31, 9, tzinfo=UTC), "monday", "false", "evening"),
            (datetime(2026, 8, 31, 14, 30, tzinfo=UTC), "monday", "false", "night"),
        )
        for now, weekday, weekend, period in cases:
            with self.subTest(now=now):
                facts = {fact.key: fact.value for fact in build_temporal_facts(timezone_name="Asia/Seoul", now=now)}
                self.assertEqual((facts["weekday"], facts["is_weekend"], facts["day_period"]), (weekday, weekend, period))

    def test_unknown_weather_is_explicit_and_never_a_positive_judgment(self) -> None:
        state = update_world_model(uuid4(), "좋은 아침", timezone_name="Asia/Seoul", now=datetime(2026, 8, 29, 1, tzinfo=UTC))
        context = build_world_model_context(state)
        self.assertIn("Weather: unknown", context)
        self.assertNotIn("good weather", context.casefold())

    def test_verified_weather_is_exact_and_stale_weather_is_not_current(self) -> None:
        now = datetime(2026, 8, 29, 1, tzinfo=UTC)
        state = update_world_model(uuid4(), "안녕", now=now)
        set_weather_reading(state, WeatherReading("clear", 25, 55, "Seoul", now, "weather_provider"))
        self.assertIn("clear, 25°C, humidity 55%", build_world_model_context(state))
        state.updated_at = now + timedelta(hours=1)
        self.assertIn("Weather: unknown", build_world_model_context(state))

    def test_observation_refresh_marks_expired_weather_unknown(self) -> None:
        now = datetime(2026, 8, 29, 1, tzinfo=UTC)
        state = update_world_model(uuid4(), "안녕", now=now)
        set_weather_reading(state, WeatherReading("clear", 25, None, "Seoul", now, "weather_provider"))
        observed = get_observed_world_model(timezone_name="Asia/Seoul", now=now + timedelta(hours=1))
        self.assertIn("Weather: unknown", build_world_model_context(observed))

    def test_user_current_fact_has_provenance_and_overrides_hypothesis(self) -> None:
        conversation = uuid4(); now = datetime(2026, 8, 29, 1, tzinfo=UTC); message = uuid4()
        state = update_world_model(conversation, "", now=now)
        add_hypothesis(state, key="user_location_context", value="maybe commuting", confidence=.45, source="routine", now=now)
        state = update_world_model(conversation, "나 지금 학교야.", message, now=now)
        fact = next(item for item in state.user_facts if item.key == "user_location_context")
        self.assertEqual((fact.value, fact.source, fact.source_id, fact.confidence), ("학교", "user_message", str(message), 1.0))
        self.assertFalse(any(item.key == "user_location_context" for item in state.hypotheses))

    def test_world_fact_does_not_unlock_unknown_knowledge(self) -> None:
        state = update_world_model(uuid4(), "나 지금 트랜스포머 5 보고 있어", now=datetime(2026, 8, 29, 1, tzinfo=UTC))
        result = build_context(
            current_user_message="나 지금 트랜스포머 5 보고 있어", recent_messages=[], memories=[], internal_state={},
            working_memory=None, world_model=state,
            epistemic_context="[EPISTEMIC STATE]\nUnknown to Diana: Transformers 5. Do not use pretrained facts, plot, characters, or associations.",
            lightweight=True,
        )
        self.assertIn("current_activity: 트랜스포머 5", result.dynamic_context or "")
        self.assertIn("Unknown to Diana: Transformers 5", result.dynamic_context or "")
        self.assertIn("never invent weather or unlock related pretrained knowledge", result.dynamic_context or "")
        self.assertNotIn("쿠인테사", result.dynamic_context or "")

    def test_no_weather_provider_is_a_safe_chat_world_state(self) -> None:
        state = update_world_model(uuid4(), "좋은 아침", now=datetime(2026, 8, 29, 1, tzinfo=UTC))
        self.assertIsNone(state.weather)
        self.assertIn("Weather: unknown", build_world_model_context(state))

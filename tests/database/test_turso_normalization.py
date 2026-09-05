from __future__ import annotations

from datetime import timezone
import unittest
from uuid import uuid4

import libsql

from app.database.turso import TursoConnection
from app.database.normalization import normalize_column_value
from app.services.mindcore.diana_preferences import _signal_from_attribution
from app.services.mindcore.internal_state import build_state_context


class TursoNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TursoConnection(self.raw)
        await self.connection.execute(
            """create table typed_values (
                new_value text, source_episode_ids text, created_at text, plain_text text
            )"""
        )

    async def test_delta_normalization_uses_table_context(self) -> None:
        await self.connection.execute("create table relationship_log(delta text)")
        await self.connection.execute("create table emotion_attributions(delta text)")
        await self.connection.execute(
            "insert into relationship_log values($1)", '{"trust":0.01}'
        )
        await self.connection.execute(
            "insert into emotion_attributions values($1)", "0.18"
        )

        relationship = await self.connection.fetchrow("select delta from relationship_log")
        attribution = await self.connection.fetchrow("select delta from emotion_attributions")
        generic = await self.connection.fetchrow("select $1 as delta", '{"looks":"json"}')

        assert relationship is not None and attribution is not None and generic is not None
        self.assertEqual(relationship["delta"], {"trust": 0.01})
        self.assertEqual(attribution["delta"], 0.18)
        self.assertIsInstance(attribution["delta"], float)
        self.assertEqual(
            normalize_column_value(
                "delta", 0.25, source_tables=frozenset({"emotion_attributions"})
            ),
            0.25,
        )
        self.assertEqual(generic["delta"], '{"looks":"json"}')

    async def test_numeric_emotion_delta_builds_context_and_preference_signal(self) -> None:
        attribution = {
            "emotion_attribution_id": uuid4(),
            "emotion": "curiosity",
            "delta": 0.18,
            "cause_summary": "User asked a substantive question.",
            "confidence": 0.70,
        }
        context = build_state_context(
            {
                "emotion": "curiosity",
                "emotion_intensity": 0.18,
                "emotion_vector": {"curiosity": 0.68},
                "stress": 0.0,
            },
            [attribution],
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("curiosity increased", context)
        signal = _signal_from_attribution(attribution)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.value, 0.12)

    async def asyncTearDown(self) -> None:
        self.raw.close()

    async def test_explicit_json_columns_decode_but_plain_text_is_not_guessed(self) -> None:
        await self.connection.execute(
            "insert into typed_values values($1,$2,$3,$4)",
            '{"chosen":"A","confidence":0.8}',
            '["episode-a","episode-b"]',
            "2026-08-27T06:30:00Z",
            '{"looks":"json"}',
        )
        row = await self.connection.fetchrow("select * from typed_values")
        assert row is not None
        self.assertEqual(row["new_value"], {"chosen": "A", "confidence": 0.8})
        self.assertEqual(row["source_episode_ids"], ["episode-a", "episode-b"])
        self.assertEqual(row["plain_text"], '{"looks":"json"}')

    async def test_offsetless_timestamp_is_normalized_as_utc(self) -> None:
        await self.connection.execute(
            "insert into typed_values values(null,null,$1,null)", "2026-08-27 06:30:00"
        )
        row = await self.connection.fetchrow("select * from typed_values")
        assert row is not None
        self.assertEqual(row["created_at"].tzinfo, timezone.utc)
        self.assertEqual(row["created_at"].isoformat(), "2026-08-27T06:30:00+00:00")

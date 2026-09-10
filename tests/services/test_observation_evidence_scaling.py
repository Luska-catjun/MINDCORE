from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import unittest

import libsql

from app.database.turso import TursoConnection
from app.routers.observe import observe_narratives, observe_preferences, observe_self_model


class TrackingConnection(TursoConnection):
    """Records only evidence result sizes; it never records private row contents."""

    def __init__(self, connection: libsql.Connection) -> None:
        super().__init__(connection)
        self.evidence_fetches: list[tuple[str, int]] = []

    async def fetch(self, statement: str, *args: object) -> list[dict]:
        rows = await super().fetch(statement, *args)
        normalized = " ".join(statement.casefold().split())
        for table in (
            "diana_preference_evidence",
            "diana_narrative_evidence",
            "diana_self_model_evidence",
        ):
            if f"from {table}" in normalized:
                self.evidence_fetches.append((table, len(rows)))
                break
        return rows


class LocalObservationPool:
    def __init__(self) -> None:
        self.raw = libsql.connect(":memory:")
        self.connection = TrackingConnection(self.raw)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    def close(self) -> None:
        self.raw.close()


class ObservationEvidenceScalingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalObservationPool()
        self.now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """create table diana_preferences (
                    diana_preference_id text primary key, subject_key text, display_name text,
                    status text, affinity real, confidence real, evidence_count integer,
                    positive_evidence integer, negative_evidence integer, curiosity_evidence integer,
                    first_observed_at text, last_observed_at text, stabilized_at text
                )"""
            )
            await connection.execute(
                """create table preferences (
                    preference_id text primary key, subject text, value text, preference_type text,
                    status text, confidence real, evidence_count integer, first_seen_at text,
                    last_seen_at text, owner_type text
                )"""
            )
            await connection.execute(
                """create table diana_preference_evidence (
                    id text primary key, diana_preference_id text, signal_type text, signal_value real,
                    source_experience_id text, episode_id text, created_at text
                )"""
            )
            await connection.execute(
                """create table diana_narratives (
                    id text primary key, status text, confidence real, last_observed_at text,
                    evidence_count integer
                )"""
            )
            await connection.execute(
                """create table diana_narrative_evidence (
                    id text primary key, narrative_id text, episode_id text, decision_id text,
                    preference_evidence_id text, emotion_attribution_id text, memory_id text,
                    relationship_log_id text, knowledge_id text, evidence_type text,
                    signal_value real, created_at text
                )"""
            )
            await connection.execute(
                """create table diana_self_model (
                    id text primary key, status text, confidence real, last_reinforced_at text
                )"""
            )
            await connection.execute(
                """create table diana_self_model_evidence (
                    id text primary key, self_model_id text, evidence_type text, direction text,
                    weight real, source_narrative_id text, source_preference_id text,
                    source_decision_id text, source_episode_id text, source_conversation_id text,
                    created_at text
                )"""
            )

    async def asyncTearDown(self) -> None:
        self.pool.close()

    async def _insert_preference(self, parent_id: str, *, index: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into diana_preferences values(
                    $1,$2,$2,'stable',.5,.8,50,50,0,0,$3,$3,null
                )""",
                parent_id,
                f"subject-{index}",
                self.now - timedelta(minutes=index),
            )

    async def _insert_preference_evidence(self, parent_id: str, *, index: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into diana_preference_evidence values(
                    $1,$2,'positive',$3,$4,null,$5
                )""",
                f"{parent_id}-evidence-{index:03d}",
                parent_id,
                float(index),
                f"experience-{parent_id}-{index}",
                self.now - timedelta(seconds=index),
            )

    async def _insert_narrative(self, parent_id: str, *, index: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                "insert into diana_narratives values($1,'emerging',.7,$2,2)",
                parent_id,
                self.now - timedelta(minutes=index),
            )

    async def _insert_narrative_evidence(self, parent_id: str, *, index: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into diana_narrative_evidence values(
                    $1,$2,null,null,null,null,$3,null,null,'memory',$4,$5
                )""",
                f"{parent_id}-evidence-{index:03d}",
                parent_id,
                f"memory-{parent_id}-{index}",
                float(index),
                self.now - timedelta(seconds=index),
            )

    async def _insert_self_model(self, parent_id: str, *, index: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                "insert into diana_self_model values($1,'emerging',.7,$2)",
                parent_id,
                self.now - timedelta(minutes=index),
            )

    async def _insert_self_model_evidence(self, parent_id: str, *, index: int) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """insert into diana_self_model_evidence values(
                    $1,$2,'narrative','support',.5,$3,null,null,null,null,$4
                )""",
                f"{parent_id}-evidence-{index:03d}",
                parent_id,
                f"narrative-{parent_id}-{index}",
                self.now - timedelta(seconds=index),
            )

    async def test_preferences_evidence_is_bounded_to_display_limit_not_entire_table(self) -> None:
        async with self.pool.connection.transaction():
            for parent_index in range(200):
                parent_id = f"preference-{parent_index:03d}"
                await self._insert_preference(parent_id, index=parent_index)
                for evidence_index in range(50):
                    await self._insert_preference_evidence(parent_id, index=evidence_index)
        self.pool.connection.evidence_fetches.clear()

        observed = await observe_preferences(evidence_limit=5, pool=self.pool)

        self.assertEqual(len(observed["diana_preferences"]), 200)
        self.assertTrue(all(len(item["recent_evidence"]) == 5 for item in observed["diana_preferences"]))
        self.assertEqual(self.pool.connection.evidence_fetches, [("diana_preference_evidence", 1000)])

    async def test_narrative_evidence_uses_only_current_parent_page_and_preserves_order(self) -> None:
        async with self.pool.connection.transaction():
            for parent_index in range(3):
                parent_id = f"narrative-{parent_index}"
                await self._insert_narrative(parent_id, index=parent_index)
                for evidence_index in range(2):
                    await self._insert_narrative_evidence(parent_id, index=evidence_index)
        self.pool.connection.evidence_fetches.clear()

        first_page = await observe_narratives(limit=1, offset=0, pool=self.pool)
        second_page = await observe_narratives(limit=1, offset=1, pool=self.pool)

        self.assertEqual([item["id"] for item in first_page["items"]], ["narrative-0"])
        self.assertEqual([item["id"] for item in second_page["items"]], ["narrative-1"])
        self.assertEqual(
            [item["memory_id"] for item in first_page["items"][0]["evidence"]],
            ["memory-narrative-0-0", "memory-narrative-0-1"],
        )
        self.assertEqual(self.pool.connection.evidence_fetches, [
            ("diana_narrative_evidence", 2),
            ("diana_narrative_evidence", 2),
        ])

    async def test_self_model_evidence_uses_only_current_parent_page_and_preserves_order(self) -> None:
        async with self.pool.connection.transaction():
            for parent_index in range(3):
                parent_id = f"self-{parent_index}"
                await self._insert_self_model(parent_id, index=parent_index)
                for evidence_index in range(2):
                    await self._insert_self_model_evidence(parent_id, index=evidence_index)
        self.pool.connection.evidence_fetches.clear()

        first_page = await observe_self_model(limit=1, offset=0, pool=self.pool)
        second_page = await observe_self_model(limit=1, offset=1, pool=self.pool)

        self.assertEqual([item["id"] for item in first_page["items"]], ["self-0"])
        self.assertEqual([item["id"] for item in second_page["items"]], ["self-1"])
        self.assertEqual(
            [item["source_narrative_id"] for item in first_page["items"][0]["evidence"]],
            ["narrative-self-0-0", "narrative-self-0-1"],
        )
        self.assertEqual(self.pool.connection.evidence_fetches, [
            ("diana_self_model_evidence", 2),
            ("diana_self_model_evidence", 2),
        ])

    async def test_empty_parent_pages_do_not_query_evidence(self) -> None:
        self.pool.connection.evidence_fetches.clear()

        self.assertEqual((await observe_narratives(limit=1, offset=0, pool=self.pool))["items"], [])
        self.assertEqual((await observe_self_model(limit=1, offset=0, pool=self.pool))["items"], [])

        self.assertEqual(self.pool.connection.evidence_fetches, [])

    async def test_persona_database_isolation_keeps_narrative_evidence_separate(self) -> None:
        await self._insert_narrative("persona-a", index=0)
        await self._insert_narrative_evidence("persona-a", index=0)
        other_pool = LocalObservationPool()
        try:
            async with other_pool.acquire() as connection:
                await connection.execute(
                    """create table diana_narratives (
                        id text primary key, status text, confidence real, last_observed_at text,
                        evidence_count integer
                    )"""
                )
                await connection.execute(
                    """create table diana_narrative_evidence (
                        id text primary key, narrative_id text, episode_id text, decision_id text,
                        preference_evidence_id text, emotion_attribution_id text, memory_id text,
                        relationship_log_id text, knowledge_id text, evidence_type text,
                        signal_value real, created_at text
                    )"""
                )
                await connection.execute(
                    "insert into diana_narratives values('persona-b','emerging',.7,$1,1)",
                    self.now,
                )
                await connection.execute(
                    """insert into diana_narrative_evidence values(
                        'persona-b-evidence','persona-b',null,null,null,null,'memory-persona-b',
                        null,null,'memory',.5,$1
                    )""",
                    self.now,
                )

            observed_a = await observe_narratives(limit=50, offset=0, pool=self.pool)
            observed_b = await observe_narratives(limit=50, offset=0, pool=other_pool)

            self.assertEqual([item["id"] for item in observed_a["items"]], ["persona-a"])
            self.assertEqual([item["id"] for item in observed_b["items"]], ["persona-b"])
            self.assertEqual(observed_a["items"][0]["evidence"][0]["memory_id"], "memory-persona-a-0")
            self.assertEqual(observed_b["items"][0]["evidence"][0]["memory_id"], "memory-persona-b")
        finally:
            other_pool.close()


if __name__ == "__main__":
    unittest.main()

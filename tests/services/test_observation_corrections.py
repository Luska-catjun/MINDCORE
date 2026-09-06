from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore import observation_corrections as corrections
from app.services.mindcore.narrative import get_narrative_snapshot, hydrate_narrative_snapshot
from app.services.mindcore.snapshot_scope import CognitiveSnapshotScope


class Connection:
    def __init__(self, found: bool = True) -> None:
        self.found = found
        self.calls: list[str] = []

    async def fetchrow(self, statement, *args):
        self.calls.append(statement)
        return {"id": str(args[-1])} if self.found else None

    async def execute(self, statement, *args):
        self.calls.append(statement)

    @asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class LocalPool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class ObservationCorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_narrative_delete_is_removed_from_runtime_snapshot(self) -> None:
        pool = LocalPool()
        scope = CognitiveSnapshotScope()
        item_id = uuid4()
        async with pool.acquire() as connection:
            await connection.execute(
                """create table diana_narratives(
                       id text primary key, narrative_key text, subject_key text, category text,
                       summary text, status text, confidence real, evidence_count integer,
                       distinct_episode_count integer, distinct_conversation_count integer,
                       first_observed_at text, last_observed_at text, created_at text, updated_at text)"""
            )
            await connection.execute(
                """insert into diana_narratives values(
                       $1,'activity:test','test','activity_pattern','stale narrative','emerging',.7,
                       4,2,2,'old','old','old','old')""",
                item_id,
            )
        await hydrate_narrative_snapshot(pool, scope)
        self.assertEqual([str(row["id"]) for row in get_narrative_snapshot(scope)], [str(item_id)])

        await corrections.delete_narrative(pool, item_id, scope)

        async with pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select count(*) from diana_narratives"), 0)
        self.assertEqual(get_narrative_snapshot(scope), ())

    async def test_memory_correction_normalizes_content_without_scoring_changes(self) -> None:
        connection = Connection()
        await corrections.update_memory(Pool(connection), uuid4(), "  Corrected memory  ")
        statement = connection.calls[0].lower()
        self.assertIn("update memories set content", statement)
        self.assertNotIn("importance", statement)
        self.assertNotIn("memory_strength", statement)

    async def test_empty_correction_is_rejected_before_storage(self) -> None:
        connection = Connection()
        with self.assertRaises(ValueError):
            await corrections.update_knowledge(Pool(connection), uuid4(), "  ")
        self.assertEqual(connection.calls, [])

    async def test_missing_item_is_not_silently_corrected(self) -> None:
        with self.assertRaises(KeyError):
            await corrections.update_self_model(Pool(Connection(found=False)), uuid4(), "A correction")

    async def test_delete_preference_removes_evidence_before_parent(self) -> None:
        connection = Connection()
        await corrections.delete_persona_preference(Pool(connection), uuid4())
        self.assertIn("diana_preference_evidence", connection.calls[1])
        self.assertIn("diana_preferences", connection.calls[2])

    async def test_real_isolated_storage_round_trips_each_durable_correction(self) -> None:
        pool = LocalPool()
        async with pool.acquire() as connection:
            await connection.execute("create table memories(memory_id text primary key, content text not null, normalized_content text not null unique, updated_at text not null)")
            await connection.execute("create table diana_knowledge(knowledge_id text primary key, summary text not null, updated_at text not null)")
            await connection.execute("create table diana_knowledge_facts(knowledge_id text not null)")
            await connection.execute("create table diana_preferences(diana_preference_id text primary key, display_name text not null, updated_at text not null)")
            await connection.execute("create table diana_preference_evidence(diana_preference_id text not null)")
            await connection.execute("create table diana_self_model(id text primary key, summary text not null, updated_at text not null)")
            await connection.execute("create table diana_narratives(id text primary key, summary text not null, updated_at text not null)")
            ids = [uuid4() for _ in range(5)]
            await connection.execute("insert into memories values($1,$2,$3,$4)", ids[0], "before", "before", "old")
            await connection.execute("insert into diana_knowledge values($1,$2,$3)", ids[1], "before", "old")
            await connection.execute("insert into diana_preferences values($1,$2,$3)", ids[2], "before", "old")
            await connection.execute("insert into diana_self_model values($1,$2,$3)", ids[3], "before", "old")
            await connection.execute("insert into diana_narratives values($1,$2,$3)", ids[4], "before", "old")

        await corrections.update_memory(pool, ids[0], " corrected memory ")
        await corrections.update_knowledge(pool, ids[1], "corrected knowledge")
        await corrections.update_persona_preference(pool, ids[2], "corrected preference")
        await corrections.update_self_model(pool, ids[3], "corrected self model")
        await corrections.update_narrative(pool, ids[4], "corrected narrative")

        async with pool.acquire() as connection:
            self.assertEqual(await connection.fetchval("select content from memories where memory_id=$1", ids[0]), "corrected memory")
            self.assertEqual(await connection.fetchval("select summary from diana_knowledge where knowledge_id=$1", ids[1]), "corrected knowledge")
            self.assertEqual(await connection.fetchval("select display_name from diana_preferences where diana_preference_id=$1", ids[2]), "corrected preference")
            self.assertEqual(await connection.fetchval("select summary from diana_self_model where id=$1", ids[3]), "corrected self model")
            self.assertEqual(await connection.fetchval("select summary from diana_narratives where id=$1", ids[4]), "corrected narrative")

        await corrections.delete_memory(pool, ids[0])
        await corrections.delete_knowledge(pool, ids[1])
        await corrections.delete_persona_preference(pool, ids[2])
        await corrections.delete_self_model(pool, ids[3])
        await corrections.delete_narrative(pool, ids[4])
        async with pool.acquire() as connection:
            for table, column, item_id in (("memories", "memory_id", ids[0]), ("diana_knowledge", "knowledge_id", ids[1]), ("diana_preferences", "diana_preference_id", ids[2]), ("diana_self_model", "id", ids[3]), ("diana_narratives", "id", ids[4])):
                self.assertIsNone(await connection.fetchrow(f"select * from {table} where {column}=$1", item_id))

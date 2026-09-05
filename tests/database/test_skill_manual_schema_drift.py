from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import unittest
import libsql

from app.database.turso import TursoConnection
from scripts.apply_turso_skill_manual_migration import apply_skill_manual_migration


class Pool:
    def __init__(self): self.connection = TursoConnection(libsql.connect(":memory:"))
    @asynccontextmanager
    async def acquire(self): yield self.connection


class SkillManualSchemaDriftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = Pool()
        async with self.pool.acquire() as c:
            await c.execute("pragma foreign_keys=on")
            await c.execute("create table conversations(conversation_id text primary key)")
            # Production-compatible legacy shape, before active_skill.
            legacy = Path("db/migrations/017_working_memory_v01.sql").read_text().replace(",\'active_skill\'", "")
            for statement in legacy.split(";"):
                if statement.strip(): await c.execute(statement)
            await c.execute("insert into conversations values($1)", uuid4())
            conversation_id = await c.fetchval("select conversation_id from conversations")
            for slot in ("active_topic", "open_loop", "active_memory_ref"):
                await c.execute("insert into diana_working_memory_items(id,conversation_id,slot_type,item_key,summary,source_type,salience,status,created_at,last_touched_at,expires_at) values($1,$2,$3,$4,$5,$6,.8,\'active\',$7,$7,$7)", uuid4(), conversation_id, slot, slot, slot, "test", "2026-01-01T00:00:00+00:00")

    async def test_rebuild_preserves_legacy_rows_and_accepts_active_skill(self):
        async with self.pool.acquire() as c:
            self.assertEqual(await apply_skill_manual_migration(c), "SKILL_MANUAL_TURSO_MIGRATION_OK")
            self.assertEqual(await c.fetchval("select count(*) from diana_working_memory_items"), 3)
            conversation_id = await c.fetchval("select conversation_id from conversations")
            await c.execute("insert into diana_working_memory_items(id,conversation_id,slot_type,item_key,summary,source_type,salience,status,created_at,last_touched_at,expires_at) values($1,$2,\'active_skill\',\'game.updown\',\'game.updown\',\'skill_manual\',1,\'active\',$3,$3,$3)", uuid4(), conversation_id, "2026-01-01T00:00:00+00:00")
            self.assertEqual(await c.fetchval("select slot_type from diana_working_memory_items where item_key=\'game.updown\'"), "active_skill")
            self.assertEqual(await c.fetch("pragma foreign_key_check"), [])
            self.assertEqual(await apply_skill_manual_migration(c), "SKILL_MANUAL_TURSO_MIGRATION_ALREADY_APPLIED")

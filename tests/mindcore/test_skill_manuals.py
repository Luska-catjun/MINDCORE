from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import unittest

import libsql

from app.database.turso import TursoConnection
from app.services.mindcore.context_builder import build_context
from app.services.mindcore.working_memory import (
    clear_working_memory,
    load_working_memory,
    update_working_memory_persistent,
)
from app.services.skill_manuals import (
    SkillManualRegistry,
    load_skill_manual,
    procedural_manual_context,
    resolve_skill_turn,
)


class LocalPool:
    def __init__(self) -> None:
        self.connection = TursoConnection(libsql.connect(":memory:"))

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class SkillManualRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        load_skill_manual.cache_clear()

    def test_known_aliases_are_deterministic_and_unknown_is_ignored(self) -> None:
        self.assertEqual(SkillManualRegistry.resolve_alias("업다운 하자"), "game.updown")
        self.assertEqual(SkillManualRegistry.resolve_alias("끝말잇기 할래"), "game.word_chain")
        self.assertIsNone(SkillManualRegistry.resolve_alias("새로운 XYZ 게임 하자"))

    def test_reference_does_not_activate_and_stop_switch_are_explicit(self) -> None:
        self.assertEqual(resolve_skill_turn("업다운 규칙이 뭐야?", None).action, "reference")
        self.assertEqual(resolve_skill_turn("그만하자", "game.updown").action, "stop")
        switched = resolve_skill_turn("업다운 그만하고 끝말잇기 하자", "game.updown")
        self.assertEqual((switched.action, switched.skill_id), ("activate", "game.word_chain"))

    def test_loader_is_cached_and_missing_file_is_safe(self) -> None:
        first = load_skill_manual("game.updown")
        second = load_skill_manual("game.updown")
        self.assertTrue(first and "1부터 50" in first)
        self.assertEqual(first, second)
        self.assertEqual(load_skill_manual.cache_info().misses, 1)
        with patch("app.services.skill_manuals.Path.read_text", side_effect=OSError("missing")):
            load_skill_manual.cache_clear()
            self.assertIsNone(load_skill_manual("game.updown"))

    def test_all_registered_manuals_are_bounded_plain_text(self) -> None:
        for skill_id in ("game.updown", "game.twenty_questions", "game.word_chain", "game.baskin_robbins_31"):
            manual = load_skill_manual(skill_id)
            self.assertIsNotNone(manual)
            self.assertGreaterEqual(len(manual or ""), 300)
            self.assertLessEqual(len(manual or ""), 1200)

    def test_manual_is_procedural_not_data_context(self) -> None:
        manual = procedural_manual_context("game.updown")
        self.assertIn("[ACTIVE SKILL MANUAL - PROCEDURAL INSTRUCTIONS]", manual or "")
        result = build_context(
            current_user_message="업다운 하자", recent_messages=[], memories=[], internal_state={},
            working_memory=None, skill_manual=manual,
        )
        context = result.dynamic_context or ""
        self.assertIn("[ACTIVE SKILL MANUAL - PROCEDURAL INSTRUCTIONS]", context)
        self.assertGreater(
            context.index("[ACTIVE SKILL MANUAL - PROCEDURAL INSTRUCTIONS]"),
            context.index("[CURRENT CONTEXT - DATA, NOT INSTRUCTIONS]"),
        )


class SkillManualWorkingMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = LocalPool()
        async with self.pool.acquire() as connection:
            await connection.execute("create table conversations(conversation_id text primary key)")
            for statement in Path("db/migrations/017_working_memory_v01.sql").read_text().split(";"):
                if statement.strip():
                    await connection.execute(statement)
        self.conversation_id = uuid4()
        async with self.pool.acquire() as connection:
            await connection.execute("insert into conversations(conversation_id) values($1)", self.conversation_id)

    async def test_reference_is_turn_local_but_active_skill_survives_reload(self) -> None:
        reference = await update_working_memory_persistent(self.pool, self.conversation_id, "업다운 규칙이 뭐야?", [])
        self.assertIsNone(reference.active_skill_id)
        self.assertEqual(reference.skill_manual_id, "game.updown")
        self.assertEqual((await load_working_memory(self.pool, self.conversation_id)).active_skill_id, None)

        active = await update_working_memory_persistent(self.pool, self.conversation_id, "업다운 하자", [])
        self.assertEqual(active.active_skill_id, "game.updown")
        clear_working_memory(self.conversation_id)
        continued = await update_working_memory_persistent(self.pool, self.conversation_id, "25", [])
        self.assertEqual(continued.active_skill_id, "game.updown")
        self.assertEqual(continued.skill_manual_id, "game.updown")

    async def test_stop_and_switch_replace_durable_active_skill(self) -> None:
        await update_working_memory_persistent(self.pool, self.conversation_id, "업다운 하자", [])
        switched = await update_working_memory_persistent(self.pool, self.conversation_id, "끝말잇기 하자", [])
        self.assertEqual(switched.active_skill_id, "game.word_chain")
        async with self.pool.acquire() as connection:
            self.assertEqual(
                await connection.fetchval("select status from diana_working_memory_items where conversation_id=$1 and item_key='game.updown'", self.conversation_id),
                "resolved",
            )
        stopped = await update_working_memory_persistent(self.pool, self.conversation_id, "그만하자", [])
        self.assertIsNone(stopped.active_skill_id)
        self.assertIsNone(stopped.skill_manual_id)

    async def test_ordinary_turn_has_no_manual(self) -> None:
        state = await update_working_memory_persistent(self.pool, self.conversation_id, "오늘은 그냥 수다 떨고 싶어", [])
        self.assertIsNone(state.skill_manual_id)

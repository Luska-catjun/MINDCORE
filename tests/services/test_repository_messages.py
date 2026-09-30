from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from app.database.turso import TursoPool
from app.models.enums import MessageRole
from app.schemas.messages import MessageCreate
from app.services.repository import create_message, list_messages


class _ExpiryConnection:
    def __init__(self, pool, attempt: int) -> None:
        self.pool = pool
        self.attempt = attempt
        self.pending = None

    @asynccontextmanager
    async def transaction(self):
        self.attempt = self.pool.next_write_attempt()
        yield
        mode = self.pool.commit_modes[self.attempt] if self.attempt < len(self.pool.commit_modes) else "commit"
        if mode == "expired_after_commit":
            self.pool.rows[self.pending["id"]] = self.pending
            raise ValueError("Hrana STREAM_EXPIRED")
        if mode == "expired_before_commit":
            raise ValueError("Hrana STREAM_EXPIRED")
        if self.pending is not None:
            self.pool.rows[self.pending["id"]] = self.pending

    async def fetchrow(self, statement, *args):
        if statement.lstrip().casefold().startswith("insert"):
            self.pool.insert_ids.append(args[0])
            self.pending = {
                "id": args[0], "conversation_id": args[1], "role": args[2],
                "source_device": args[3], "sequence": 1, "content": args[-2],
                "created_at": args[-1],
            }
            return self.pending
        return self.pool.rows.get(args[0])


class _ExpiryPool:
    def __init__(self, commit_modes) -> None:
        self.commit_modes = commit_modes
        self.rows = {}
        self.insert_ids = []
        self.write_lock = asyncio.Lock()
        self.write_attempts = 0

    @asynccontextmanager
    async def message_write_scope(self):
        async with self.write_lock:
            yield

    @asynccontextmanager
    async def acquire(self):
        yield _ExpiryConnection(self, -1)

    def next_write_attempt(self):
        value = self.write_attempts
        self.write_attempts += 1
        return value


class RepositoryMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.pool = TursoPool(str(Path(self.directory.name) / "messages.db"), "test-token")
        self.conversation_a, self.conversation_b = uuid4(), uuid4()
        async with self.pool.acquire() as connection:
            await connection.execute("create table conversations(conversation_id text primary key)")
            await connection.execute("""create table messages (
                id text primary key, conversation_id text not null references conversations(conversation_id),
                sequence integer not null check(sequence > 0), role text not null, content text not null,
                source_device text not null, created_at text not null, unique(conversation_id, sequence)
            )""")
            await connection.execute("insert into conversations values($1)", self.conversation_a)
            await connection.execute("insert into conversations values($1)", self.conversation_b)

    async def asyncTearDown(self) -> None:
        await self.pool.close()
        self.directory.cleanup()

    def _message(self, conversation_id, content: str, *, sequence: int | None = None) -> MessageCreate:
        return MessageCreate(
            conversation_id=conversation_id, role=MessageRole.user, content=content,
            source_device="test", sequence=sequence, metadata={"test": True},
        )

    async def test_sequence_is_conversation_local_and_returned_with_message(self) -> None:
        first = await create_message(self.pool, self._message(self.conversation_a, "one"))
        second = await create_message(self.pool, self._message(self.conversation_a, "two"))
        other = await create_message(self.pool, self._message(self.conversation_b, "other"))
        self.assertEqual((first["sequence"], second["sequence"], other["sequence"]), (1, 2, 1))
        self.assertEqual(first["content"], "one")
        self.assertEqual(first["metadata"], {"test": True})
        self.assertEqual([item["sequence"] for item in await list_messages(self.pool, self.conversation_a)], [1, 2])

    async def test_delete_gap_keeps_existing_max_plus_one_behavior(self) -> None:
        rows = [await create_message(self.pool, self._message(self.conversation_a, f"message-{index}")) for index in range(3)]
        async with self.pool.acquire() as connection:
            await connection.execute("delete from messages where id=$1", rows[-1]["id"])
        replacement = await create_message(self.pool, self._message(self.conversation_a, "replacement"))
        self.assertEqual(replacement["sequence"], 3)

    async def test_explicit_sequence_is_preserved_and_next_auto_sequence_uses_current_max(self) -> None:
        explicit = await create_message(self.pool, self._message(self.conversation_a, "explicit", sequence=7))
        automatic = await create_message(self.pool, self._message(self.conversation_a, "automatic"))
        self.assertEqual((explicit["sequence"], automatic["sequence"]), (7, 8))

    async def test_concurrent_auto_sequence_inserts_are_unique_and_ordered(self) -> None:
        rows = await asyncio.gather(
            create_message(self.pool, self._message(self.conversation_a, "first")),
            create_message(self.pool, self._message(self.conversation_a, "second")),
        )
        self.assertEqual(sorted(row["sequence"] for row in rows), [1, 2])
        listed = await list_messages(self.pool, self.conversation_a)
        self.assertEqual([item["sequence"] for item in listed], [1, 2])
        self.assertEqual(len({item["sequence"] for item in listed}), 2)

    async def test_fifty_concurrent_auto_sequence_inserts_are_unique_and_ordered(self) -> None:
        rows = await asyncio.gather(*(
            create_message(self.pool, self._message(self.conversation_a, f"burst-{index}"))
            for index in range(50)
        ))
        listed = await list_messages(self.pool, self.conversation_a)
        self.assertEqual(len(rows), 50)
        self.assertEqual([item["sequence"] for item in listed], list(range(1, 51)))
        self.assertEqual(len({str(item["id"]) for item in listed}), 50)

    async def test_ambiguous_commit_reconciles_stable_id_without_retry(self) -> None:
        pool = _ExpiryPool(["expired_after_commit"])
        row = await create_message(pool, self._message(self.conversation_a, "ambiguous"))
        self.assertEqual(len(pool.insert_ids), 1)
        self.assertEqual(str(row["id"]), str(pool.insert_ids[0]))
        self.assertEqual(len(pool.rows), 1)

    async def test_expired_uncommitted_write_retries_same_id_once(self) -> None:
        pool = _ExpiryPool(["expired_before_commit", "commit"])
        row = await create_message(pool, self._message(self.conversation_a, "retry"))
        self.assertEqual(len(pool.insert_ids), 2)
        self.assertEqual(pool.insert_ids[0], pool.insert_ids[1])
        self.assertEqual(str(row["id"]), str(pool.insert_ids[0]))
        self.assertEqual(len(pool.rows), 1)

    async def test_expired_write_retry_budget_is_bounded(self) -> None:
        pool = _ExpiryPool(["expired_before_commit"] * 3)
        with self.assertRaisesRegex(ValueError, "STREAM_EXPIRED"):
            await create_message(pool, self._message(self.conversation_a, "bounded"))
        self.assertEqual(len(pool.insert_ids), 3)
        self.assertEqual(len(set(pool.insert_ids)), 1)
        self.assertEqual(len(pool.rows), 0)

    async def test_exhausted_expired_stream_does_not_poison_next_operation(self) -> None:
        pool = _ExpiryPool(["expired_before_commit"] * 3)
        with self.assertRaisesRegex(ValueError, "STREAM_EXPIRED"):
            await create_message(pool, self._message(self.conversation_a, "expires"))
        pool.commit_modes.append("commit")
        row = await create_message(pool, self._message(self.conversation_a, "fresh stream"))
        self.assertEqual(row["content"], "fresh stream")
        self.assertEqual(len(pool.rows), 1)

    async def test_latest_window_returns_202_message_tail_in_display_order(self) -> None:
        async with self.pool.acquire() as connection:
            for sequence in range(1, 203):
                await connection.execute(
                    """insert into messages(id, conversation_id, sequence, role, content, source_device, created_at)
                       values($1,$2,$3,$4,$5,$6,$7)""",
                    uuid4(), self.conversation_a, sequence, "user", f"message-{sequence}", "test", f"2026-01-01T00:00:{sequence:03d}Z",
                )

        oldest_page = await list_messages(self.pool, self.conversation_a, limit=200, offset=0)
        latest_page = await list_messages(self.pool, self.conversation_a, limit=200, offset=0, latest=True)

        self.assertEqual([item["sequence"] for item in oldest_page], list(range(1, 201)))
        self.assertEqual([item["sequence"] for item in latest_page], list(range(3, 203)))

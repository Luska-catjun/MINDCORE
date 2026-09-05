from __future__ import annotations

import sqlite3
import unittest


class LifecycleFixture(unittest.TestCase):
    """Small isolated graph that mirrors the production message lifecycle."""

    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute("pragma foreign_keys=on")
        self.db.executescript("""
        create table conversations(id text primary key);
        create table messages(id text primary key, conversation_id text not null references conversations(id) on delete cascade);
        create table experiences(id text primary key, conversation_id text not null references conversations(id), user_message_id text not null references messages(id) on delete cascade);
        create table episodes(id text primary key, user_message_id text references messages(id) on delete set null, experience_id text references experiences(id) on delete set null);
        create table memories(id text primary key, source_message_id text references messages(id) on delete set null);
        create table knowledge_facts(id text primary key, source_message_id text references messages(id) on delete set null);
        create table emotion_attributions(id text primary key, source_experience_id text references experiences(id) on delete set null);
        create table relationship_log(id text primary key, source_experience_id text references experiences(id) on delete set null);
        """)
        self.db.executescript("""
        insert into conversations values ('c');
        insert into messages values ('m','c');
        insert into experiences values ('x','c','m');
        insert into episodes values ('e','m','x');
        insert into memories values ('memory','m');
        insert into knowledge_facts values ('fact','m');
        insert into emotion_attributions values ('emotion','x');
        insert into relationship_log values ('relationship','x');
        """)

    def tearDown(self) -> None:
        self.db.close()

    def test_set_null_foreign_keys_are_nullable(self) -> None:
        for (table,) in self.db.execute("select name from sqlite_master where type='table'"):
            columns = {row[1]: row[3] for row in self.db.execute(f'pragma table_info("{table}")')}
            for fk in self.db.execute(f'pragma foreign_key_list("{table}")'):
                if fk[6].upper() == "SET NULL":
                    self.assertEqual(columns[fk[3]], 0, f"{table}.{fk[3]} is SET NULL + NOT NULL")

    def test_message_delete_preserves_durable_provenance(self) -> None:
        self.db.execute("delete from messages where id='m'")
        self.assertIsNone(self.db.execute("select 1 from messages where id='m'").fetchone())
        self.assertIsNone(self.db.execute("select 1 from experiences where id='x'").fetchone())
        self.assertEqual(self.db.execute("select user_message_id,experience_id from episodes where id='e'").fetchone(), (None, None))
        self.assertIsNone(self.db.execute("select source_message_id from memories where id='memory'").fetchone()[0])
        self.assertIsNone(self.db.execute("select source_message_id from knowledge_facts where id='fact'").fetchone()[0])
        self.assertIsNone(self.db.execute("select source_experience_id from emotion_attributions where id='emotion'").fetchone()[0])
        self.assertIsNone(self.db.execute("select source_experience_id from relationship_log where id='relationship'").fetchone()[0])
        self.assertEqual(self.db.execute("pragma foreign_key_check").fetchall(), [])

    def test_conversation_delete_cascades_message_owned_experience(self) -> None:
        """NO ACTION is not a blocker because message CASCADE removes Experience first."""
        self.db.execute("delete from conversations where id='c'")
        self.assertIsNone(self.db.execute("select 1 from messages where id='m'").fetchone())
        self.assertIsNone(self.db.execute("select 1 from experiences where id='x'").fetchone())
        self.assertEqual(self.db.execute("select user_message_id,experience_id from episodes where id='e'").fetchone(), (None, None))

from __future__ import annotations
from contextlib import asynccontextmanager
import unittest,libsql
from app.database.turso import TursoConnection
from scripts.bootstrap_turso import bootstrap
class Pool:
 def __init__(self): self.c=TursoConnection(libsql.connect(':memory:'))
 @asynccontextmanager
 async def acquire(self): yield self.c
class BootstrapTests(unittest.IsolatedAsyncioTestCase):
 async def test_empty_second_run_and_partial_protection(self):
  p=Pool()
  async with p.acquire() as c:
   self.assertEqual(await bootstrap(c),'TURSO_BOOTSTRAP_OK version=21')
   self.assertEqual(await c.fetchval("select value from schema_metadata where key='turso_baseline_version'"),'21')
   self.assertEqual(await c.fetch('pragma foreign_key_check'),[])
   self.assertEqual(await bootstrap(c),'TURSO_BOOTSTRAP_ALREADY_INITIALIZED')
  partial=Pool()
  async with partial.acquire() as c:
   await c.execute('create table conversations(conversation_id text primary key)')
   with self.assertRaisesRegex(RuntimeError,'PARTIAL_OR_UNKNOWN'): await bootstrap(c)

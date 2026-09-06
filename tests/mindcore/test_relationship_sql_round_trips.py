from __future__ import annotations
from contextlib import asynccontextmanager
from uuid import uuid4
import unittest
import libsql
from app.database.turso import TursoConnection
from app.services.mindcore.relationship import RelationshipState, update_relationship_from_experience

class C(TursoConnection):
 def __init__(self,c): super().__init__(c);self.ops=[];self.fail=False
 async def execute(self,s,*a):
  q=' '.join(s.casefold().split())
  if 'from experiences' in q:self.ops.append('window')
  elif q.startswith('insert into relationship_log'):self.ops.append('log')
  elif q.startswith('select familiarity,trust,affection,conflict,updated_at from relationship'):self.ops.append('state_read')
  elif q.startswith('update relationship_log set'):self.ops.append('log_update')
  elif q.startswith('update relationship set'):
   self.ops.append('state')
   if self.fail:raise RuntimeError('injected relationship update failure')
  return await super().execute(s,*a)
class P:
 def __init__(self):self.c=C(libsql.connect(':memory:'))
 @asynccontextmanager
 async def acquire(self):yield self.c
class RelationshipSqlTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.p=P();self.e=uuid4()
  async with self.p.acquire() as c:
   await c.execute('create table relationship(id integer primary key,familiarity real,trust real,affection real,conflict real,updated_at text)')
   await c.execute("insert into relationship values(1,.1,.3,.15,0,'2026-01-01T00:00:00+00:00')")
   await c.execute('create table experiences(experience_id text primary key,outcome_type text,created_at text)')
   await c.execute("insert into experiences values($1,'neutral','2026-01-01T00:00:00+00:00')",self.e)
   await c.execute('create table relationship_log(relationship_log_id text primary key,source_experience_id text unique,previous_state text,delta text,new_state text,reason text,created_at text)')
 async def test_authoritative_insert_first_operations_and_duplicate_noop(self):
  text='같이 해줘서 고마워. 덕분에 진짜 편하다.'
  result=await update_relationship_from_experience(self.p,self.e,user_text=text,current_state=RelationshipState(.1,.3,.15,0))
  self.assertAlmostEqual(result.familiarity,.1072);self.assertEqual(self.p.c.ops,['log','state_read','state','log_update'])
  self.p.c.ops.clear();self.assertIsNone(await update_relationship_from_experience(self.p,self.e,user_text=text,current_state=RelationshipState(.1072,.3084,.1602,0)));self.assertEqual(self.p.c.ops,['log'])
 async def test_log_rolls_back_when_state_update_fails(self):
  self.p.c.fail=True
  with self.assertRaisesRegex(RuntimeError,'injected relationship update failure'):await update_relationship_from_experience(self.p,self.e,user_text='이건 믿고 맡길게.',current_state=RelationshipState(.1,.3,.15,0))
  async with self.p.acquire() as c:self.assertEqual(await c.fetchval('select count(*) from relationship_log'),0)
 async def test_distinct_stale_snapshots_chain_from_authoritative_relationship(self):
  other=uuid4()
  async with self.p.acquire() as c:await c.execute("insert into experiences values($1,'neutral','2026-01-01T00:00:00+00:00')",other)
  stale=RelationshipState(.1,.3,.15,0)
  await update_relationship_from_experience(self.p,self.e,user_text='이건 믿고 맡길게.',current_state=stale)
  await update_relationship_from_experience(self.p,other,user_text='이건 믿고 맡길게.',current_state=stale)
  async with self.p.acquire() as c:
   self.assertAlmostEqual(float(await c.fetchval('select trust from relationship where id=1')),.3194628)
   rows=await c.fetch('select previous_state,new_state from relationship_log order by rowid')
  self.assertEqual(len(rows),2)
  self.assertAlmostEqual(float(rows[0]['previous_state']['trust']),.3)
  self.assertAlmostEqual(float(rows[0]['new_state']['trust']),.3098)
  self.assertAlmostEqual(float(rows[1]['previous_state']['trust']),.3098)
  self.assertAlmostEqual(float(rows[1]['new_state']['trust']),.3194628)
 async def test_ordinary_turn_skips_relationship_sql_entirely(self):
  self.p.c.ops.clear()
  self.assertIsNone(await update_relationship_from_experience(self.p,self.e,user_text='점심 먹었어.',current_state=RelationshipState(.1,.3,.15,0)))
  self.assertEqual(self.p.c.ops,[])

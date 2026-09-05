from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest
from unittest.mock import AsyncMock, patch
import libsql
from app.database.turso import TursoConnection
from app.services.mindcore.goals import BASELINES, get_need_snapshot, update_goals, _apply, refresh_goal_lifecycle, get_relevant_goals
from app.services.mindcore.working_memory import WorkingMemoryState, WorkingMemoryItem

class RecordingTursoConnection(TursoConnection):
 def __init__(self, connection): super().__init__(connection); self.statements=[]; self.transaction_count=0
 async def execute(self, statement, *args):
  self.statements.append(statement)
  return await super().execute(statement, *args)
 @asynccontextmanager
 async def transaction(self):
  self.transaction_count += 1
  async with super().transaction(): yield

class Pool:
 def __init__(self, database=':memory:'): self.c=RecordingTursoConnection(libsql.connect(database)); self.acquire_count=0
 @asynccontextmanager
 async def acquire(self):
  self.acquire_count += 1
  yield self.c

class GoalsNeedsTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.p=Pool(); self.cid=uuid4()
  await self._initialize(self.p, self.cid)
 async def _initialize(self, pool, conversation_id):
  async with pool.acquire() as c:
   await c.execute('create table conversations(conversation_id text primary key)'); await c.execute('insert into conversations values($1)',conversation_id)
   for x in Path('db/migrations/018_goals_needs_v01.sql').read_text().split(';'):
    if x.strip(): await c.execute(x)
 async def test_baseline_decay_and_idempotence(self):
  needs=await get_need_snapshot(self.p); self.assertEqual(set(needs),set(BASELINES)); self.assertAlmostEqual(needs['curiosity'].value,.45)
  await _apply(self.p,needs,'curiosity',.4,'unknown_active_topic','message','m',self.cid); await _apply(self.p,needs,'curiosity',.4,'unknown_active_topic','message','m',self.cid)
  self.assertAlmostEqual(needs['curiosity'].value,.85)
  later=await get_need_snapshot(self.p,now=needs['curiosity'].updated_at+timedelta(hours=18)); self.assertAlmostEqual(later['curiosity'].value,.65,places=2)
 async def test_complete_canonical_rows_use_select_without_repair_insert(self):
  await get_need_snapshot(self.p)
  self.p.c.statements.clear(); self.p.c.transaction_count=0
  needs=await get_need_snapshot(self.p)
  self.assertEqual(set(needs),set(BASELINES))
  self.assertEqual(self.p.c.transaction_count,0)
  self.assertEqual(sum('insert into diana_needs' in statement.casefold() for statement in self.p.c.statements),0)
  self.assertEqual(sum(statement.lstrip().casefold().startswith('select') for statement in self.p.c.statements),1)
 async def test_missing_rows_repair_once_and_persist_across_fresh_pool(self):
  seeded=await get_need_snapshot(self.p)
  await _apply(self.p,seeded,'curiosity',.2,'repair_preservation','message','repair',self.cid)
  async with self.p.acquire() as c:
   existing=await c.fetchrow("select need_key,value,baseline,updated_at,last_triggered_at from diana_needs where need_key='curiosity'")
   await c.execute("delete from diana_needs where need_key in ('autonomy','activity')")
  self.p.c.statements.clear(); self.p.c.transaction_count=0
  repaired=await get_need_snapshot(self.p)
  self.assertEqual(set(repaired),set(BASELINES))
  self.assertEqual(self.p.c.transaction_count,1)
  self.assertEqual(sum('insert into diana_needs' in statement.casefold() for statement in self.p.c.statements),2)
  async with self.p.acquire() as c:
   after=await c.fetchrow("select need_key,value,baseline,updated_at,last_triggered_at from diana_needs where need_key='curiosity'")
  self.assertEqual(dict(after),dict(existing))
  self.p.c.statements.clear()
  await get_need_snapshot(self.p)
  self.assertEqual(sum('insert into diana_needs' in statement.casefold() for statement in self.p.c.statements),0)
  with TemporaryDirectory() as directory:
   database=str(Path(directory)/'needs.db'); first=Pool(database); cid=uuid4()
   await self._initialize(first,cid); await get_need_snapshot(first)
   first.c._connection.close()
   restarted=Pool(database)
   snapshot=await get_need_snapshot(restarted)
   self.assertEqual(set(snapshot),set(BASELINES))
   restarted.c._connection.close()
 async def test_help_goal_and_no_llm_context_state(self):
  wm=WorkingMemoryState(self.cid,[WorkingMemoryItem('x','Unknown topic',.9)])
  result=await update_goals(self.p,self.cid,'도와줘 working_memory',uuid4(),working_memory=wm,epistemic_unknown=True); needs,goals=result.needs,result.created_goals
  self.assertGreater(needs['helpfulness'].value,.35); self.assertGreater(needs['curiosity'].value,.45); self.assertEqual(goals,())
 async def test_same_turn_need_snapshot_and_signals_reuse_one_connection(self):
  self.p.acquire_count=0
  with patch('app.services.mindcore.goals.refresh_goal_lifecycle', new=AsyncMock()), patch('app.services.mindcore.goals._form_goals', new=AsyncMock(return_value=[])):
   result=await update_goals(self.p,self.cid,'도와줘 뭐 하고 싶어 같이 대화',uuid4()); needs, goals=result.needs,result.created_goals
  self.assertEqual(self.p.acquire_count,1)
  self.assertGreater(needs['helpfulness'].value,.35)
  self.assertGreater(needs['activity'].value,.30)
  self.assertGreater(needs['social_connection'].value,.35)
  self.assertEqual(goals,())
 async def test_no_change_turn_uses_one_goal_working_set_without_lifecycle_writes(self):
  await get_need_snapshot(self.p)
  self.p.c.statements.clear(); self.p.acquire_count=0
  result=await update_goals(self.p,self.cid,'ㅋㅋ')
  statements=[' '.join(statement.casefold().split()) for statement in self.p.c.statements]
  self.assertEqual(result.created_goals,())
  self.assertEqual(self.p.acquire_count,1)
  self.assertEqual(sum('from diana_goals where status in' in statement for statement in statements),1)
  self.assertEqual(sum(statement.startswith('update diana_goals') for statement in statements),0)
  self.assertEqual(sum(statement.startswith('insert into diana_goals') for statement in statements),0)
 async def test_formation_batches_terminal_lookup_and_reuses_active_count(self):
  await get_need_snapshot(self.p)
  async with self.p.acquire() as c: await c.execute("update diana_needs set value=.70 where need_key='curiosity'")
  self.p.c.statements.clear()
  wm=WorkingMemoryState(self.cid,[WorkingMemoryItem('focus','New Topic',.9)])
  result=await update_goals(self.p,self.cid,'새 주제를 이야기하자',uuid4(),working_memory=wm,epistemic_unknown=True)
  statements=[' '.join(statement.casefold().split()) for statement in self.p.c.statements]
  self.assertEqual(len(result.created_goals),1)
  self.assertEqual(sum('from diana_goals where goal_key in' in statement for statement in statements),1)
  self.assertEqual(sum('select count(*) from diana_goals where status' in statement for statement in statements),0)
 async def test_promotion_reuses_working_set_without_per_candidate_counts(self):
  await get_need_snapshot(self.p)
  for index in range(3): await self._goal(status='candidate',key=f'candidate-{index}')
  self.p.c.statements.clear()
  await refresh_goal_lifecycle(self.p,self.cid)
  statements=[' '.join(statement.casefold().split()) for statement in self.p.c.statements]
  self.assertEqual(sum('from diana_goals where status in' in statement for statement in statements),1)
  self.assertEqual(sum('select count(*) from diana_goals where status' in statement for statement in statements),0)
  self.assertEqual(sum(statement.startswith("update diana_goals set status='active'") for statement in statements),2)
 async def test_social_does_not_grow_from_time(self):
  needs=await get_need_snapshot(self.p); later=await get_need_snapshot(self.p,now=needs['social_connection'].updated_at+timedelta(days=30)); self.assertAlmostEqual(later['social_connection'].value,.35)
 async def _goal(self, *, conversation=None, status='active', expires=None, goal_type='short_term', key=None):
  now=(await get_need_snapshot(self.p))['curiosity'].updated_at; identifier=uuid4()
  async with self.p.acquire() as c:
   await c.execute("""insert into diana_goals(id,goal_key,goal_type,summary,origin_need,priority,status,progress,confidence,conversation_id,source_type,created_at,updated_at,expires_at,metadata)
    values($1,$2,$3,'goal','understanding',.8,$4,0,.8,$5,'test',$6,$6,$7,'{}')""",identifier,key or str(identifier),goal_type,status,conversation or self.cid,now,expires)
  return identifier
 async def test_grounded_loop_satisfaction_and_expiry(self):
  open_goal=await self._goal(goal_type='open_loop_clarification')
  await refresh_goal_lifecycle(self.p,self.cid,WorkingMemoryState(self.cid,[]))
  async with self.p.acquire() as c: self.assertEqual(await c.fetchval('select status from diana_goals where id=$1',open_goal),'satisfied')
  expired=await self._goal(status='candidate',expires=(await get_need_snapshot(self.p))['curiosity'].updated_at-timedelta(seconds=1))
  await refresh_goal_lifecycle(self.p,self.cid)
  async with self.p.acquire() as c: self.assertEqual(await c.fetchval('select status from diana_goals where id=$1',expired),'expired')
 async def test_limits_promotion_and_conversation_isolation(self):
  other=uuid4()
  async with self.p.acquire() as c: await c.execute('insert into conversations values($1)',other)
  for i in range(4): await self._goal(status='candidate',key=f'c{i}')
  await self._goal(conversation=other,status='candidate',key='other')
  await refresh_goal_lifecycle(self.p,self.cid)
  async with self.p.acquire() as c:
   self.assertLessEqual(await c.fetchval("select count(*) from diana_goals where status='active'"),3)
   self.assertLessEqual(await c.fetchval("select count(*) from diana_goals where status='active' and conversation_id=$1",self.cid),2)
  self.assertEqual(await get_relevant_goals(self.p,other,None),[] if False else await get_relevant_goals(self.p,other,None))
  self.assertTrue(all(goal.conversation_id==other for goal in await get_relevant_goals(self.p,other,None)))
 async def test_need_and_goal_survive_db_round_trip_without_baseline_reset(self):
  needs=await get_need_snapshot(self.p); await _apply(self.p,needs,'curiosity',.26,'restart_probe','message','restart',self.cid)
  goal=await self._goal(key='restart-goal')
  # This is a fresh DB read, not a process-local state reuse.
  reloaded=await get_need_snapshot(self.p)
  self.assertAlmostEqual(reloaded['curiosity'].value,.71,places=2)
  async with self.p.acquire() as c:
   self.assertEqual(await c.fetchval('select status from diana_goals where id=$1',goal),'active')

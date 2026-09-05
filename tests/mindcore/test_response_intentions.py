from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import unittest, libsql
from app.database.turso import TursoConnection
from app.services.mindcore.intentions import select_response_intention, render_intention_context
from app.services.mindcore.working_memory import WorkingMemoryState, WorkingMemoryItem
from app.services.mindcore.goals import Goal, get_need_snapshot
from app.services.mindcore.knowledge import detect_subjects, build_epistemic_context

class Pool:
 def __init__(self): self.c=TursoConnection(libsql.connect(':memory:'))
 @asynccontextmanager
 async def acquire(self): yield self.c
class IntentionsTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.p=Pool();self.cid=uuid4();self.mid=uuid4()
  async with self.p.acquire() as c:
   await c.execute('create table conversations(conversation_id text primary key)');await c.execute('create table messages(id text primary key)');await c.execute('insert into conversations values($1)',self.cid);await c.execute('insert into messages values($1)',self.mid)
   for path in ('db/migrations/018_goals_needs_v01.sql','db/migrations/019_response_intentions_v01.sql'):
    for x in Path(path).read_text().split(';'):
     if x.strip(): await c.execute(x)
 async def select(self,text,wm=None,goals=()): return await select_response_intention(self.cid,self.mid,text,working_memory=wm,relevant_goals=goals)
 async def test_user_actions_win(self):
  self.assertEqual((await self.select('지금 몇 시야?')).action,'answer')
  self.assertEqual((await self.select('이 문장 요약해줘')).action,'fulfill_request')
  self.assertEqual((await self.select('A랑 B 중 골라봐')).action,'choose')
  self.assertEqual((await self.select('그거 수정해줘')).action,'fulfill_request')
  self.assertEqual((await self.select('그거 해')).action,'clarify')
 async def test_acknowledge_provider_independent_and_compact_context(self):
  first=await self.select('나 집 왔어.');second=await self.select('나 집 왔어.')
  self.assertEqual(first,second);self.assertEqual(first.action,'acknowledge')
  rendered=render_intention_context(first);self.assertIn('RESPONSE INTENTION',rendered);self.assertNotIn('curiosity=',rendered)
 async def test_goal_followup_requires_matching_focus(self):
  await get_need_snapshot(self.p)
  now='2026-01-01T00:00:00Z'
  goal_id=uuid4()
  async with self.p.acquire() as c: await c.execute("insert into diana_goals values($1,'g','short_term','Learn more about World Model','curiosity',.8,'active',0,.8,$2,'test',null,$3,$3,null,'{}')",goal_id,self.cid,now)
  goal=Goal(str(goal_id),'g','Learn more about World Model','curiosity',.8,'active',0,.8,self.cid,'test',None,None,None)
  self.assertEqual((await self.select('ㅋㅋ',WorkingMemoryState(self.cid,[WorkingMemoryItem('w','World Model',.9)]),(goal,))).action,'ask_followup')
  self.assertEqual((await self.select('ㅋㅋ',WorkingMemoryState(self.cid,[WorkingMemoryItem('w','school',.9)]),(goal,))).action,'acknowledge')
 def test_unknown_explanation_request_is_detected_and_restricted(self):
  subject=detect_subjects('인공지능이 뭔지 설명해줘')
  self.assertEqual(subject[0].canonical_name,'인공지능')
  context=build_epistemic_context([{'canonical_name':'인공지능','status':'unknown'}]) or ''
  self.assertIn('Do not use pretrained facts',context)

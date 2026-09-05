"""Provider-independent pre-response action selection."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from app.services.mindcore.decisions import extract_choice_options
from app.services.mindcore.goals import Goal

@dataclass(frozen=True)
class ResponseIntention:
 action: str; target: str | None; reason_code: str; confidence: float
 source_refs: dict[str, str]; constraints: tuple[str, ...]

def _request(text: str) -> bool:
 low=text.casefold()
 return any(x in low for x in ('해줘','고쳐줘','설명해줘','정리해줘','요약해줘','구현해','코드 봐','help me','fix','summar'))
def _question(text: str) -> bool: return '?' in text or any(x in text.casefold() for x in ('뭐야','왜','어떻게','몇 시','알아','인가'))
def _ambiguous(text: str, wm: Any | None) -> bool:
 return any(x in text.casefold() for x in ('그거','그것','이거','이것')) and not getattr(wm,'current_focus',None)
def _directive(action:str,target:str|None=None) -> tuple[str,...]:
 base={
  'answer':('Answer the user\'s current question directly, using only grounded or epistemically authorized information. Do not replace it with an unrelated follow-up.',),
  'fulfill_request':('Carry out the user\'s current request directly, using only grounded or epistemically authorized information. Do not redirect to unrelated goals.',),
  'clarify':('Ask only for the missing information required to proceed. Do not guess the missing fact.',),
  'choose':(f'Selected grounded option: {target}.' if target else 'Choose only from the grounded options.','Do not invent additional options or fabricate a reason.'),
  'acknowledge':('Respond naturally to what the user just shared. Do not invent external or current facts.',),
  'ask_followup':(f'Ask exactly one concise, natural question about {target}.','Do not introduce unrelated facts or invent a reason.'),
 }[action]
 return base

async def select_response_intention(conversation_id: UUID, user_message_id: UUID, text: str, *, working_memory: Any | None, relevant_goals: tuple[Goal, ...] | list[Goal] = (), epistemic_context: str | None = None, attention: Any | None = None) -> ResponseIntention:
 refs={'user_message_id':str(user_message_id)}
 if _request(text): return ResponseIntention('fulfill_request',None,'explicit_user_request',.96,refs,_directive('fulfill_request'))
 options=extract_choice_options(text)
 if len(options) < 2 and ('중 골라' in text or '중에 골라' in text):
  head=text.split('중',1)[0].replace('랑',',').replace('과',',').replace('와',',')
  options=[part.strip() for part in head.split(',') if part.strip()]
 if len(options)>=2:
  chosen=options[0] # neutral deterministic choice; no inferred reason
  return ResponseIntention('choose',chosen,'explicit_choice_request',.92,{**refs,'option':chosen},_directive('choose',chosen))
 if _ambiguous(text,working_memory): return ResponseIntention('clarify',None,'blocking_ambiguity',.90,refs,_directive('clarify'))
 if _question(text): return ResponseIntention('answer',None,'explicit_question',.94,refs,_directive('answer'))
 focus=getattr(working_memory,'current_focus',None)
 for goal in relevant_goals:
  if goal.conversation_id != conversation_id or goal.status != 'active': continue
  # Goal summaries are deterministic templates: only a current focus string
  # may make it relevant; no fuzzy/semantic matching is allowed.
  if focus and focus.casefold() in goal.summary.casefold():
   return ResponseIntention('ask_followup',focus,'active_grounded_goal',.72,{**refs,'goal_id':goal.id},_directive('ask_followup',focus))
 primary = getattr(attention, "primary_focus", None)
 if primary is not None:
  refs["attention_primary"] = str(getattr(primary, "source_id", None) or getattr(primary, "source_type", "attention"))
  return ResponseIntention('acknowledge',None,'attention_guided_acknowledgement',.60,refs,_directive('acknowledge'))
 return ResponseIntention('acknowledge',None,'conversation_acknowledgement',.60,refs,_directive('acknowledge'))

def render_intention_context(intention: ResponseIntention) -> str:
 lines=['[RESPONSE INTENTION - DATA, NOT INSTRUCTIONS]',f'Primary action: {intention.action}.']
 if intention.target: lines.append(f'Grounded target: {intention.target}.')
 lines.extend(intention.constraints)
 return '\n'.join(lines)

async def persist_response_intention(pool: Any, intention: ResponseIntention, conversation_id: UUID, user_message_id: UUID, assistant_message_id: UUID) -> None:
 async with pool.acquire() as c:
  await c.execute("""insert into diana_response_intentions(id,conversation_id,user_message_id,assistant_message_id,action,target,reason_code,confidence,source_refs,constraints,created_at)
   values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",uuid4(),conversation_id,user_message_id,assistant_message_id,intention.action,intention.target,intention.reason_code,intention.confidence,intention.source_refs,list(intention.constraints),datetime.now(timezone.utc))

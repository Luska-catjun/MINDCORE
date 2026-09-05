from collections import OrderedDict
from dataclasses import dataclass,field
from datetime import datetime,timezone
from time import perf_counter
from uuid import UUID
import logging
logger=logging.getLogger('diana.consolidation')
MAX_PROCESSED_IDS=200;MAX_RESULTS=50;MAX_DEFERRED=20
@dataclass(frozen=True)
class ConsolidationResult:
 experience_id:UUID;actions:list[str];reinforced_memory_ids:list[str]=field(default_factory=list);deferred_items:list[str]=field(default_factory=list);reasons:list[str]=field(default_factory=list);created_at:datetime=field(default_factory=lambda:datetime.now(timezone.utc))
_processed:OrderedDict[UUID,ConsolidationResult]=OrderedDict()
def evaluate_consolidation(experience:dict)->ConsolidationResult:
 actions=['no_op'];reasons=['No new long-term write authority belongs to consolidation.'];deferred=[]
 if experience.get('activated_memory_ids'):
  actions=['already_handled'];reasons=['Recall pipeline already reinforced selected memories.']
 if experience.get('outcome_type') in {'success','failure','correction'}:
  deferred=['goal_or_relationship_signal'];actions.append('defer')
 return ConsolidationResult(experience['experience_id'],actions,[],deferred,reasons)
async def consolidate_recent_experience(experience:dict)->ConsolidationResult:
 if experience['experience_id'] in _processed:return _processed[experience['experience_id']]
 start=perf_counter();result=evaluate_consolidation(experience);_processed[result.experience_id]=result
 while len(_processed)>MAX_PROCESSED_IDS:_processed.popitem(last=False)
 logger.info('Consolidation experience_id=%s action_count=%s deferred_count=%s latency_ms=%.2f',result.experience_id,len(result.actions),len(result.deferred_items),(perf_counter()-start)*1000)
 return result
def get_recent_consolidation_result(limit:int=50):return list(_processed.values())[-limit:]
def clear_consolidation_state():_processed.clear()

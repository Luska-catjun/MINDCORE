// src/types/api.ts
//
// 업로드받은 openapi.json을 그대로 옮긴 타입 정의.
// 필드명/타입을 절대 임의로 바꾸지 않았다 — 백엔드 스펙과 다르면
// 이 파일만 보고 바로 어디가 틀렸는지 알 수 있게 하기 위함이다.

export type ConversationStatus = "active" | "archived";

export interface ConversationCreate {
  title?: string | null;
  source_device?: string | null;
  status?: ConversationStatus;
  metadata?: Record<string, unknown>;
}

export interface ConversationRead {
  id: string;
  title: string | null;
  source_device: string | null;
  status: ConversationStatus;
  metadata: Record<string, unknown>;
  started_at: string;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
}

export type MessageRole = "user" | "diana" | "system" | "tool";

export interface MessageCreate {
  conversation_id: string;
  role: MessageRole;
  content: string;
  source_device?: string | null;
  sequence?: number | null;
  metadata?: Record<string, unknown>;
}

export interface MessageRead {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  source_device: string | null;
  sequence: number | null;
  metadata: Record<string, unknown>;
  timestamp: string;
  created_at: string;
}

export interface HealthResponse {
  status: string;
  db?: string;
  [key: string]: string | undefined;
}

export interface DianaStateRead {
  id: string | number;
  mood: string | null;
  energy: number | null;
  focus: string | null;
  values: Record<string, unknown>;
  source_device: string | null;
  created_at: string | null;
  updated_at: string;
}

export type ChatRequest = MessageCreate;

export interface ChatResponse {
  user_message: MessageRead;
  diana_message: MessageRead;
}

export interface EpisodeRead {
  id: string;
  conversation_id: string | null;
  content: string;
  importance: number;
  memory_strength: number;
  created_at: string;
}

export interface ValidationErrorDetail {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export interface HTTPValidationError {
  detail: ValidationErrorDetail[];
}

export interface ObservationList<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  query_latency_ms: number;
}

export interface ObservedMemory {
  id: string;
  content: string;
  importance: number;
  memory_strength: number;
  effective_strength: number;
  recall_frequency: number;
  created_at: string;
  last_recalled_at: string | null;
  source_episode_id: string | null;
}

export interface EmotionAttribution {
  id: string;
  emotion: string;
  delta: number;
  resulting_value: number;
  cause_type: string;
  cause_summary: string;
  source_type: string;
  source_id: string | null;
  source_experience_id: string | null;
  episode_id: string | null;
  created_at: string;
}

export interface ObserveEmotion {
  state: {
    primary_emotion: string;
    primary_intensity: number;
    emotion_vector: Record<string, number>;
    mood_valence: number;
    energy: number;
    curiosity: number;
    stress: number;
    updated_at: string;
  } | null;
  attributions: EmotionAttribution[];
  query_latency_ms: number;
}

export interface DianaPreference {
  id: string;
  subject: string;
  display_name: string;
  status: "curious" | "tentative" | "stable";
  affinity: number;
  confidence: number;
  evidence_count: number;
  positive_evidence: number;
  negative_evidence: number;
  curiosity_evidence: number;
  first_observed_at: string;
  last_observed_at: string;
  stabilized_at: string | null;
  recent_evidence: Array<{ signal_type: string; signal_value: number; experience_id: string; episode_id: string | null; created_at: string }>;
}

export interface UserPreference {
  id: string;
  subject: string;
  value: string;
  preference_type: string;
  status: string;
  confidence: number;
  evidence_count: number;
  first_seen_at: string;
  last_seen_at: string;
}

export interface ObservePreferences {
  diana_preferences: DianaPreference[];
  user_preferences: UserPreference[];
  query_latency_ms: number;
}

export interface ObservedEpisode {
  episode_id: string;
  conversation_id: string | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
  experience_id: string | null;
  episode_type: string;
  topic_key: string | null;
  provenance: string;
  is_grounded: boolean;
  started_at: string | null;
  ended_at: string | null;
  summary: string;
  emotion_count: number;
  relationship_change_count: number;
  preference_evidence_count: number;
  memory_count: number;
}

export interface ObserveRelationship {
  state: { familiarity: number; trust: number; affection: number; conflict: number; updated_at: string } | null;
  logs: Array<{ id: string; delta: Record<string, number>; reason: string; episode_id: string | null; created_at: string }>;
  query_latency_ms: number;
}

export interface ObserveStats { counts: Record<string, number>; query_latency_ms: number; }
export interface ObserveGoalsNeeds { needs: Array<{need_key:string;value:number;baseline:number;updated_at:string;last_triggered_at:string|null}>; goals: Array<{id:string;summary:string;origin_need:string;priority:number;status:string;progress:number;confidence:number;conversation_id:string|null;source_type:string;source_id:string|null;created_at:string;updated_at:string;expires_at:string|null}>; events: Array<{need_key:string;delta:number;before_value:number;after_value:number;reason:string;source_type:string;source_id:string|null;conversation_id:string|null;created_at:string}>; query_latency_ms:number; }
export interface ObservedIntention { id:string; conversation_id:string; user_message_id:string|null; assistant_message_id:string|null; action:string; target:string|null; reason_code:string; confidence:number; source_refs:Record<string,string>; constraints:string[]; created_at:string; }
export interface ObservedMessage { id:string; conversation_id:string; role:string; content:string; created_at:string; }

export interface ObservedDecision {
  id: string; decision_type: string; chosen: string | null; confidence: number | null;
  reason: string | null; options: string[] | null; conversation_id: string | null; user_message_id: string | null;
  assistant_message_id: string | null; episode_ids: string[]; status: "active" | "executed" | "superseded" | "cancelled" | "expired";
  decision_domain: string | null; updated_at: string | null; resolved_at: string | null; created_at: string;
}

export interface ObservedNarrativeEvidence {
  id: string; episode_id: string | null; decision_id: string | null; preference_evidence_id: string | null;
  emotion_attribution_id: string | null; memory_id: string | null; relationship_log_id: string | null;
  knowledge_id: string | null; evidence_type: string; signal_value: number; created_at: string;
}

export interface ObservedNarrative {
  id: string; narrative_key: string; subject_key: string; category: string; summary: string;
  status: "candidate" | "emerging" | "established"; confidence: number; evidence_count: number;
  distinct_episode_count: number; distinct_conversation_count: number; first_observed_at: string;
  last_observed_at: string; activation_eligible: boolean; attention_score: number | null; attention_reasons: string[]; evidence: ObservedNarrativeEvidence[];
}

export interface ObservedSelfModelEvidence {
  id: string; evidence_type: string; direction: "support" | "contradict"; weight: number;
  source_narrative_id: string | null; source_preference_id: string | null; source_decision_id: string | null;
  source_episode_id: string | null; source_conversation_id: string | null; created_at: string;
}

export interface ObservedSelfModel {
  id: string; claim_key: string; category: "preference_self" | "decision_tendency" | "narrative_theme";
  subject: string; summary: string; confidence: number; status: "candidate" | "emerging" | "established";
  support_count: number; contradiction_count: number; conversation_count: number; first_observed_at: string;
  last_reinforced_at: string; evidence: ObservedSelfModelEvidence[]; activation_eligible: boolean;
  independent_source_count: number; source_types: string[]; current: boolean; attention_score: number | null;
}

export interface WorldFact { key: string; value: string; source?: string; confidence?: number; observed_at?: string; expires_at?: string | null; status: string; scope?: string; source_id?: string | null; }
export interface ObserveWorldModel { temporal_facts: WorldFact[]; weather: WorldFact; current_user_facts: WorldFact[]; hypotheses: WorldFact[]; updated_at: string; storage: "ephemeral"; }

export interface ObservedKnowledge {
  id: string;
  subject_key: string;
  canonical_name: string;
  knowledge_type: string;
  summary: string;
  confidence: number;
  status: "introduced" | "known" | "well_known";
  source_type: string;
  source_id: string | null;
  source_episode_id: string | null;
  first_learned_at: string;
  last_reinforced_at: string;
  reinforcement_count: number;
  learning_session_count: number;
  fact_count: number;
  facts: Array<{
    id: string;
    fact_text: string;
    knowledge_scope: "fictional_story";
    source_type: string;
    source_message_id: string;
    source_episode_id: string | null;
    confidence: number;
    reinforcement_count: number;
    contradiction_count: number;
    first_learned_at: string;
    last_reinforced_at: string;
  }>;
}

export interface ObserveDebug {
  app_env: string;
  primary_llm_provider: string;
  fallback_provider: string | null;
  active_model: string;
  fallback_model: string | null;
  diana_timezone: string;
  identity_prompt_chars: number;
  attention: { primary: { source_type: string; source_id: string | null; label: string; score: number; reasons: string[]; temporal_role: string } | null; secondary: Array<{ source_type: string; source_id: string | null; label: string; score: number; reasons: string[]; temporal_role: string }>; items: Array<{ source_type: string; source_id: string | null; label: string; score: number; reasons: string[]; temporal_role: string }>; latency_ms: number; storage: string };
  last_context: { dynamic_context_chars: number; context_mode: string | null; updated_at: string | null };
  last_fallback_event: { timestamp: string; primary_provider: string; fallback_provider: string; error_category: string } | null;
  last_epistemic: { items: Array<{ subject_key: string; status: string }>; latency_ms: number | null; updated_at: string | null };
  episode_promotions: Array<{ timestamp: string; status: string; score: number; reasons: string[] }>;
  narrative_updates: Array<{ timestamp: string; episode_id: string; accepted: boolean; subjects: string[]; latency_ms: number }>;
  decision_captures: Array<{ timestamp: string; choice_context_detected: boolean; assistant_choice_detected: boolean; chosen: string | null; reason_valid: boolean | null; persisted: boolean; episode_linked: boolean; error_category: string | null }>;
  llm_calls: Array<{ timestamp: string; request_kind: string; provider: string; model: string; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; latency_ms: number; success: boolean; error_category: string | null }>;
}

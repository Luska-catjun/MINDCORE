// src/api/client.ts
//
// 백엔드(diana/app)와의 통신을 담당하는 단일 지점.
// 여기서 호출하는 경로/메서드/필드는 업로드받은 openapi.json과 정확히 일치한다:
//
//   GET  /health
//   POST /conversations
//   GET  /conversations?limit&offset
//   GET  /conversations/{conversation_id}/messages?limit&offset&latest
//   POST /messages
//
// 백엔드 코드는 절대 수정하지 않았고, 이 파일도 스펙에 맞춰 "따라가는" 역할만 한다.

import type {
  ChatRequest,
  ChatResponse,
  ConversationCreate,
  ConversationRead,
  EpisodeRead,
  HealthResponse,
  DianaStateRead,
  MessageCreate,
  MessageRead,
  ObserveDebug,
  ObserveGoalsNeeds,
  ObservedIntention,
  ObserveEmotion,
  ObservePreferences,
  ObserveRelationship,
  ObserveStats,
  ObservationList,
  ObservedEpisode,
  ObservedKnowledge,
  ObservedMemory,
  ObservedMessage,
  ObservedDecision,
  ObservedNarrative,
  ObservedSelfModel,
  ObserveWorldModel,
} from "../types/api";

export const LOCAL_DESKTOP_API_URL = "http://127.0.0.1:8765";

export function isDesktopRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

// Web builds retain their existing VITE_API_BASE_URL semantics. Only the
// Tauri webview selects the fixed loopback sidecar endpoint.
export const API_BASE_URL: string = isDesktopRuntime()
  ? LOCAL_DESKTOP_API_URL
  : ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8000");
const BEARER_SESSION_STORAGE_KEY = "diana_bearer_session";

function apiIsCrossOrigin(): boolean {
  try {
    return new URL(API_BASE_URL, window.location.href).origin !== window.location.origin;
  } catch {
    return false;
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type RequestOptions = RequestInit & { suppressAuthFailure?: boolean };

type ErrorRecord = Record<string, unknown>;

function isErrorRecord(value: unknown): value is ErrorRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function statusMessage(status: number): string {
  if (status === 401 || status === 403) {
    return "인증이 필요합니다. 다시 로그인해주세요.";
  }
  if (status === 429) {
    return "AI 사용량 한도에 도달했습니다. 잠시 후 다시 시도해주세요.";
  }
  if (status === 408 || status === 504) {
    return "서버 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.";
  }
  if (status >= 500) {
    return "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요.";
  }
  return `요청을 처리하지 못했습니다. (${status})`;
}

function codeMessage(code: unknown): string | undefined {
  if (typeof code !== "string") {
    return undefined;
  }

  switch (code) {
    case "quota_or_rate_limit":
    case "rate_limit":
      return "AI 사용량 한도에 도달했습니다. 잠시 후 다시 시도해주세요.";
    case "timeout":
      return "AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.";
    case "network":
      return "AI 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.";
    case "authentication":
      return "AI 서비스 인증에 문제가 있습니다. 관리자 설정을 확인해주세요.";
    case "groq_server":
    case "groq_http":
    case "model_or_api_version":
    case "configuration":
    case "prompt_configuration":
    case "empty_response":
      return "AI 서비스 오류가 발생했습니다. 잠시 후 다시 시도해주세요.";
    default:
      return undefined;
  }
}

function validationMessage(detail: unknown[]): string {
  const first = detail[0];
  if (isErrorRecord(first) && typeof first.msg === "string") {
    return `입력 내용을 확인해주세요: ${first.msg}`;
  }
  return "입력 내용을 확인해주세요.";
}

export function normalizeApiError(status: number, body: unknown): string {
  const bodyRecord = isErrorRecord(body) ? body : undefined;
  const detail = bodyRecord?.detail;
  const detailRecord = isErrorRecord(detail) ? detail : undefined;
  const structuredCode = detailRecord?.code ?? detailRecord?.category ?? bodyRecord?.code;
  const mapped = codeMessage(structuredCode);
  if (mapped) {
    return mapped;
  }
  if (detailRecord && typeof detailRecord.message === "string") {
    return detailRecord.message;
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (typeof bodyRecord?.message === "string") {
    return bodyRecord.message;
  }
  if (Array.isArray(detail)) {
    return validationMessage(detail);
  }
  return statusMessage(status);
}

let authFailureHandler: (() => void) | undefined;

function getBearerSession(): string | null {
  return window.sessionStorage.getItem(BEARER_SESSION_STORAGE_KEY);
}

function storeBearerSession(accessToken: string): void {
  window.sessionStorage.setItem(BEARER_SESSION_STORAGE_KEY, accessToken);
  if (import.meta.env.DEV) {
    console.info("[AUTH] auth data stored = sessionStorage bearer fallback.");
  }
}

export function storeDesktopSession(accessToken: string): void {
  storeBearerSession(accessToken);
}

function clearBearerSession(): void {
  window.sessionStorage.removeItem(BEARER_SESSION_STORAGE_KEY);
}

export function setAuthFailureHandler(handler: (() => void) | undefined): void {
  authFailureHandler = handler;
}

async function request<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const { suppressAuthFailure = false, ...fetchOptions } = options;
  const bearerSession = getBearerSession();
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...fetchOptions,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(bearerSession && path !== "/auth/login"
          ? { Authorization: `Bearer ${bearerSession}` }
          : {}),
        ...fetchOptions.headers,
      },
    });
  } catch {
    // 네트워크 자체가 실패한 경우(서버가 꺼져 있거나 CORS/주소 문제)
    throw new ApiError(0, "서버에 연결할 수 없습니다. 백엔드 서버가 켜져 있는지 확인해주세요.");
  }

  if (!res.ok) {
    let detail: unknown = undefined;
    try {
      detail = await res.json();
    } catch {
      // 응답 바디가 JSON이 아닌 경우 무시
    }
    if ((res.status === 401 || res.status === 403) && !suppressAuthFailure) {
      authFailureHandler?.();
    }
    if (import.meta.env.DEV) {
      console.warn("MindCore API request failed", {
        path,
        status: res.status,
        body: detail,
      });
    }
    const message = normalizeApiError(res.status, detail);
    throw new ApiError(res.status, message, detail);
  }

  // 204 No Content 등 바디가 없는 응답 대비
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  health: (): Promise<HealthResponse> => request("/health"),

  getState: (): Promise<DianaStateRead> => request("/state"),

  me: (): Promise<{ authenticated: boolean; persona_display_name: string }> =>
    request("/auth/me", { suppressAuthFailure: true }),

  login: async (password: string): Promise<{ authenticated: boolean; persona_display_name: string }> => {
    clearBearerSession();
    // Render commonly serves the static frontend and API from distinct
    // origins.  Cookies still remain enabled, but immediately retaining the
    // short-lived bearer fallback prevents a SameSite/privacy policy from
    // turning the very next /auth/me request into credential=none.
    const requestBearerFallback = apiIsCrossOrigin();
    const loginResult = await request<{ authenticated: boolean; persona_display_name: string; access_token?: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password, include_access_token: requestBearerFallback }),
      suppressAuthFailure: true,
    });

    if (requestBearerFallback) {
      if (typeof loginResult.access_token !== "string" || !loginResult.access_token) {
        throw new ApiError(503, "인증 세션을 만들지 못했습니다.");
      }
      storeBearerSession(loginResult.access_token);
    }

    try {
      return await request<{ authenticated: boolean; persona_display_name: string }>("/auth/me", { suppressAuthFailure: true });
    } catch (error) {
      if (!(error instanceof ApiError) || (error.status !== 401 && error.status !== 403)) {
        throw error;
      }
    }

    // Safari can block the backend's cross-site HttpOnly cookie. Retry only
    // then, and retain the signed fallback credential for this browser tab.
    const fallback = await request<{ authenticated: boolean; persona_display_name: string; access_token?: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password, include_access_token: true }),
      suppressAuthFailure: true,
    });
    if (typeof fallback.access_token !== "string" || !fallback.access_token) {
      throw new ApiError(503, "인증 세션을 만들지 못했습니다.");
    }
    storeBearerSession(fallback.access_token);
    return await request<{ authenticated: boolean; persona_display_name: string }>("/auth/me", { suppressAuthFailure: true });
  },

  logout: async (): Promise<{ authenticated: boolean }> => {
    try {
      return await request("/auth/logout", { method: "POST", suppressAuthFailure: true });
    } finally {
      clearBearerSession();
    }
  },

  listConversations: (
    limit = 50,
    offset = 0
  ): Promise<ConversationRead[]> =>
    request(`/conversations?limit=${limit}&offset=${offset}`),

  listEpisodes: (limit = 12, offset = 0): Promise<EpisodeRead[]> =>
    request(`/episodes?limit=${limit}&offset=${offset}`),

  createConversation: (
    payload: ConversationCreate
  ): Promise<ConversationRead> =>
    request("/conversations", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listMessages: (
    conversationId: string,
    limit = 200,
    offset = 0,
    latest = false,
  ): Promise<MessageRead[]> =>
    request(
      `/conversations/${conversationId}/messages?limit=${limit}&offset=${offset}${latest ? "&latest=true" : ""}`
    ),

  createMessage: (payload: MessageCreate): Promise<MessageRead> =>
    request("/messages", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  sendChatMessage: (payload: ChatRequest): Promise<ChatResponse> =>
    request("/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  observeMemory: (sort: "recent" | "strongest" | "most_recalled" = "recent"): Promise<ObservationList<ObservedMemory>> =>
    request(`/observe/memory?limit=50&offset=0&sort=${sort}`),
  correctMemory: (id: string, value: string): Promise<{ id: string; corrected: boolean }> => request(`/observe/memory/${id}`, { method: "PATCH", body: JSON.stringify({ value }) }),
  deleteMemory: (id: string): Promise<{ id: string; deleted: boolean }> => request(`/observe/memory/${id}`, { method: "DELETE" }),
  observeMessages: (): Promise<ObservationList<ObservedMessage>> => request("/observe/messages?limit=100&offset=0"),
  deleteMessage: (id:string): Promise<{id:string;deleted:boolean}> => request(`/messages/${id}`, {method:"DELETE"}),
  deleteEpisode: (id:string): Promise<{id:string;deleted:boolean}> => request(`/observe/episodes/${id}`, {method:"DELETE"}),
  observeEmotion: (): Promise<ObserveEmotion> => request("/observe/emotion?attribution_limit=20"),
  observePreferences: (): Promise<ObservePreferences> => request("/observe/preferences?evidence_limit=5"),
  correctPersonaPreference: (id: string, value: string): Promise<{ id: string; corrected: boolean }> => request(`/observe/preferences/persona/${id}`, { method: "PATCH", body: JSON.stringify({ value }) }),
  deletePersonaPreference: (id: string): Promise<{ id: string; deleted: boolean }> => request(`/observe/preferences/persona/${id}`, { method: "DELETE" }),
  observeEpisodes: (): Promise<ObservationList<ObservedEpisode>> => request("/observe/episodes?limit=50&offset=0"),
  observeDecisions: (): Promise<ObservationList<ObservedDecision>> => request("/observe/decisions?limit=50&offset=0"),
  observeNarratives: (): Promise<ObservationList<ObservedNarrative>> => request("/observe/narratives?limit=50&offset=0"),
  correctNarrative: (id: string, value: string): Promise<{ id: string; corrected: boolean }> => request(`/observe/narratives/${id}`, { method: "PATCH", body: JSON.stringify({ value }) }),
  deleteNarrative: (id: string): Promise<{ id: string; deleted: boolean }> => request(`/observe/narratives/${id}`, { method: "DELETE" }),
  observeSelfModel: (): Promise<ObservationList<ObservedSelfModel>> => request("/observe/self-model?limit=50&offset=0"),
  correctSelfModel: (id: string, value: string): Promise<{ id: string; corrected: boolean }> => request(`/observe/self-model/${id}`, { method: "PATCH", body: JSON.stringify({ value }) }),
  deleteSelfModel: (id: string): Promise<{ id: string; deleted: boolean }> => request(`/observe/self-model/${id}`, { method: "DELETE" }),
  observeWorldModel: (): Promise<ObserveWorldModel> => request("/observe/world-model"),
  observeRelationship: (): Promise<ObserveRelationship> => request("/observe/relationship?limit=20"),
  observeKnowledge: (): Promise<ObservationList<ObservedKnowledge>> => request("/observe/knowledge?limit=50&offset=0&sort=recent"),
  correctKnowledge: (id: string, value: string): Promise<{ id: string; corrected: boolean }> => request(`/observe/knowledge/${id}`, { method: "PATCH", body: JSON.stringify({ value }) }),
  deleteKnowledge: (id: string): Promise<{ id: string; deleted: boolean }> => request(`/observe/knowledge/${id}`, { method: "DELETE" }),
  observeStats: (): Promise<ObserveStats> => request("/observe/stats"),
  observeGoalsNeeds: (): Promise<ObserveGoalsNeeds> => request("/observe/goals-needs"),
  observeIntentions: (): Promise<ObservationList<ObservedIntention>> => request("/observe/intentions?limit=50"),
  observeDebug: (): Promise<ObserveDebug> => request("/observe/debug"),
};

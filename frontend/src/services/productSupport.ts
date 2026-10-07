import { invoke } from "@tauri-apps/api/core";
import config from "../product-support.json";
import { CURRENT_VERSION } from "../releaseNotes";
import { CATEGORY_LABELS, DIAGNOSTIC_RULES, validFeedbackEndpoint } from "./feedbackContract";
export type Diagnostics = Partial<Record<"platform" | "app_version" | "os_version" | "architecture" | "provider" | "backend_status" | "persona_connection" | "update_state", string>>;
export type FeedbackPayload = { schemaVersion: 1; category: keyof typeof CATEGORY_LABELS; title: string; message: string; platform: "desktop"; appVersion: string; diagnostics?: Record<string, string> };
export function feedbackEndpoint(): string | null { return import.meta.env.VITE_MINDCORE_FEEDBACK_ENDPOINT?.trim() || config.feedbackEndpoint || null; }
export function safePublicUrl(value: string, httpsOnly = false): boolean {
  try { const u = new URL(value); if (httpsOnly && [...u.searchParams.keys()].some(key => ["token", "access_token", "api_key", "auth", "password", "signature", "sig"].includes(key.toLowerCase()))) return false; return (httpsOnly ? u.protocol === "https:" : ["http:", "https:"].includes(u.protocol)) && !!u.hostname && !u.username && !u.password; } catch { return false; }
}
export async function openPublicUrl(value: string): Promise<void> {
  if (!safePublicUrl(value)) throw new Error("링크를 열 수 없습니다.");
  await invoke("open_public_url", { url: value });
}
function validate(payload: FeedbackPayload): string {
  if (payload.schemaVersion !== 1 || !(Object.hasOwn(CATEGORY_LABELS, payload.category)) || payload.platform !== "desktop" || payload.appVersion !== CURRENT_VERSION
    || !payload.title.trim() || !payload.message.trim() || payload.title.length > 160 || payload.message.length > 6000
    || Object.keys(payload).some(key => !["schemaVersion","category","title","message","platform","appVersion","diagnostics"].includes(key))) throw new Error("입력 내용을 확인해 주세요.");
  if (payload.diagnostics && (JSON.stringify(payload.diagnostics).length > 1024 || Object.entries(payload.diagnostics).some(([key,value]) => typeof value !== "string" || (!DIAGNOSTIC_RULES[key]?.test(value) || value !== value.trim())))) throw new Error("진단 정보를 확인해 주세요.");
  const body = JSON.stringify(payload);
  if (new TextEncoder().encode(body).length > 32768) throw new Error("입력 내용을 확인해 주세요.");
  return body;
}
export function feedbackPayload(category: string, title: string, message: string, includeDiagnostics: boolean, diagnostics: Diagnostics): FeedbackPayload {
  const canonical = Object.entries(CATEGORY_LABELS).find(([, label]) => label === category)?.[0] as FeedbackPayload["category"];
  const payload: FeedbackPayload = { schemaVersion: 1, category: canonical, title: title.trim(), message: message.trim(), platform: "desktop", appVersion: CURRENT_VERSION };
  if (title.length > 160 || message.length > 6000) throw new Error("입력 내용을 확인해 주세요.");
  if (includeDiagnostics) {
    payload.diagnostics = {};
    const names = {os_version:"osVersion",architecture:"architecture",provider:"provider",backend_status:"backendStatus",persona_connection:"personaConnection",update_state:"updaterState"};
    for (const [input, output] of Object.entries(names)) {
      const value = diagnostics[input as keyof Diagnostics];
      if (typeof value === "string" && DIAGNOSTIC_RULES[output].test(value) && value === value.trim()) payload.diagnostics[output] = value;
    }
  }
  validate(payload); return payload;
}
export function feedbackComposeUrl(payload: FeedbackPayload, issueUrl = config.feedbackIssueUrl): string {
  validate(payload);
  if (!safePublicUrl(issueUrl, true)) throw new Error("피드백 전송 경로를 확인해 주세요.");
  const url = new URL(issueUrl);
  url.searchParams.set("title", `[${CATEGORY_LABELS[payload.category]}] ${payload.title}`);
  url.searchParams.set("body", `${payload.message}\n\nplatform: ${payload.platform}\nappVersion: ${payload.appVersion}` + (payload.diagnostics ? "\n\n## 기본 진단 정보\n" + Object.entries(payload.diagnostics).map(([key, value]) => `${key}: ${value}`).join("\n") : ""));
  return url.toString();
}
async function accepted(response: Response): Promise<void> {
  if (![200,202].includes(response.status) || response.redirected || !/^application\/json(?:\s*;|$)/i.test(response.headers.get("Content-Type") || "") || !response.body) throw new Error("FEEDBACK_RESPONSE_INVALID");
  const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
  try {
    for (;;) { const { done, value } = await reader.read(); if (done) break; size += value.length; if (size > 4096) throw new Error("FEEDBACK_RESPONSE_INVALID"); chunks.push(value); }
  } finally { await reader.cancel(); }
  const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  const json = new TextDecoder("utf-8", {fatal:true}).decode(bytes);
  const whitespace = "[ \\t\\r\\n]*", id = '\"feedbackId\"' + whitespace + ":" + whitespace + '\"[A-Za-z0-9_-]{1,128}\"', ok = '\"ok\"' + whitespace + ":" + whitespace + "true";
  if (!new RegExp("^" + whitespace + "\\{" + whitespace + "(?:" + ok + "(?:" + whitespace + "," + whitespace + id + ")?|" + id + whitespace + "," + whitespace + ok + ")" + whitespace + "\\}" + whitespace + "$").test(json)) throw new Error("FEEDBACK_RESPONSE_INVALID");
  const result = JSON.parse(json);
  if (!result || Object.keys(result).some(key => !["ok","feedbackId"].includes(key)) || result.ok !== true || (result.feedbackId !== undefined && (typeof result.feedbackId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(result.feedbackId)))) throw new Error("FEEDBACK_RESPONSE_INVALID");
}
export async function submitFeedback(payload: FeedbackPayload, endpoint: string | null = feedbackEndpoint(), open = openPublicUrl): Promise<"sent" | "browser"> {
  const body = validate(payload);
  if (!endpoint) { await open(feedbackComposeUrl(payload)); return "browser"; }
  if (!validFeedbackEndpoint(endpoint)) throw new Error("피드백 전송 경로를 확인해 주세요.");
  try {
    const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body, redirect: "error", credentials: "omit", signal: AbortSignal.timeout(15000) });
    await accepted(response); return "sent";
  } catch { throw new Error("피드백을 전송하지 못했습니다."); }
}

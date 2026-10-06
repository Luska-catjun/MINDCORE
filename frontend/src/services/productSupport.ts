import { invoke } from "@tauri-apps/api/core";
import config from "../product-support.json";
export type Diagnostics = Partial<Record<"platform" | "app_version" | "os_version" | "provider" | "persona_connection" | "update_state", string>>;
export type FeedbackPayload = { category: string; title: string; message: string; diagnostics?: Diagnostics };
const categories = ["버그", "기능 제안", "사용성/UI", "기타"];
export function safePublicUrl(value: string, httpsOnly = false): boolean {
  try { const u = new URL(value); if (httpsOnly && [...u.searchParams.keys()].some(key => ["token", "access_token", "api_key", "auth", "password", "signature", "sig"].includes(key.toLowerCase()))) return false; return (httpsOnly ? u.protocol === "https:" : ["http:", "https:"].includes(u.protocol)) && !!u.hostname && !u.username && !u.password; } catch { return false; }
}
export async function openPublicUrl(value: string): Promise<void> {
  if (!safePublicUrl(value)) throw new Error("링크를 열 수 없습니다.");
  await invoke("open_public_url", { url: value });
}
export function feedbackPayload(category: string, title: string, message: string, includeDiagnostics: boolean, diagnostics: Diagnostics): FeedbackPayload {
  if (!categories.includes(category) || !title.trim() || !message.trim() || title.length > 160 || message.length > 6000) throw new Error("입력 내용을 확인해 주세요.");
  const payload: FeedbackPayload = { category, title: title.trim(), message: message.trim() };
  if (includeDiagnostics) {
    payload.diagnostics = {};
    for (const key of ["platform", "app_version", "os_version", "provider", "persona_connection", "update_state"] as const) {
      if (typeof diagnostics[key] === "string") payload.diagnostics[key] = diagnostics[key]!.slice(0, 100);
    }
  }
  return payload;
}
export function feedbackComposeUrl(payload: FeedbackPayload, issueUrl = config.feedbackIssueUrl): string {
  if (!safePublicUrl(issueUrl, true)) throw new Error("피드백 전송 경로를 확인해 주세요.");
  const url = new URL(issueUrl);
  url.searchParams.set("title", `[${payload.category}] ${payload.title}`);
  url.searchParams.set("body", payload.message + (payload.diagnostics ? "\n\n## 기본 진단 정보\n" + Object.entries(payload.diagnostics).map(([key, value]) => `${key}: ${value}`).join("\n") : ""));
  return url.toString();
}
export async function submitFeedback(payload: FeedbackPayload, endpoint: string | null = config.feedbackEndpoint, open = openPublicUrl): Promise<"sent" | "browser"> {
  if (!endpoint) { await open(feedbackComposeUrl(payload)); return "browser"; }
  if (!safePublicUrl(endpoint, true)) throw new Error("피드백 전송 경로를 확인해 주세요.");
  const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), redirect: "error", credentials: "omit", signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw new Error("피드백을 전송하지 못했습니다.");
  return "sent";
}

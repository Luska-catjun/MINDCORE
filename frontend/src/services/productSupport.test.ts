import { describe, it, expect, vi, afterEach } from "vitest";
import { feedbackPayload, feedbackComposeUrl, safePublicUrl, submitFeedback } from "./productSupport";
describe("Feedback transport and privacy", () => {
 afterEach(() => vi.unstubAllGlobals());
 const diagnostics = {platform:"desktop", app_version:"0.4.0", API_KEY:"synthetic-secret", DATABASE_URL:"libsql://private", identity:"private"};
 it("includes no diagnostics by default and allowlists consented metadata", () => {
  expect(feedbackPayload("버그","제목","내용",false,diagnostics).diagnostics).toBeUndefined();
  const payload = feedbackPayload("버그","제목","내용",true,diagnostics); expect(payload.diagnostics).toEqual({platform:"desktop",app_version:"0.4.0"}); expect(JSON.stringify(payload)).not.toMatch(/synthetic-secret|private/);
 });
 it("fallback composes the visible payload and reports browser rather than sent", async () => {
  const payload=feedbackPayload("기타","제목","내용",false,{}); const open=vi.fn().mockResolvedValue(undefined);
  expect(await submitFeedback(payload,null,open)).toBe("browser"); expect(new URL(feedbackComposeUrl(payload)).searchParams.get("body")).toBe("내용"); expect(open).toHaveBeenCalledOnce();
 });
 it("direct HTTPS success and failure use no authentication and preserve payload", async () => {
  const payload=feedbackPayload("버그","제목","내용",false,{}); const fetch=vi.fn().mockResolvedValue({ok:true}); vi.stubGlobal("fetch",fetch);
  expect(await submitFeedback(payload,"https://example.com/feedback")).toBe("sent"); expect(fetch.mock.calls[0][1].credentials).toBe("omit"); expect(fetch.mock.calls[0][1].body).toBe(JSON.stringify(payload));
  fetch.mockResolvedValue({ok:false}); await expect(submitFeedback(payload,"https://example.com/feedback")).rejects.toThrow("피드백을 전송하지 못했습니다.");
 });
 it("rejects unsafe URI schemes and credentialed URLs", () => { for (const url of ["javascript:alert(1)","file:///tmp/secret","data:text/html,x","mindcore://secret","https://user:secret@example.com"]) expect(safePublicUrl(url)).toBe(false); expect(safePublicUrl("https://example.com")).toBe(true); expect(safePublicUrl("http://example.com")).toBe(true); });
});

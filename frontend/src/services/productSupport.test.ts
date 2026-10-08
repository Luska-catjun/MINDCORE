import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { feedbackPayload, feedbackComposeUrl, safePublicUrl, submitFeedback, feedbackEndpoint } from "./productSupport";
import { validFeedbackEndpoint } from "./feedbackContract";
vi.mock("../product-support.json", () => ({ default: { feedbackEndpoint: null, feedbackIssueUrl: "https://github.com/example/mindcore/issues/new" } }));
const payload = () => feedbackPayload("버그","제목","내용",false,{});
const response = (body: string, status=200, type="application/json") => new Response(body,{status,headers:{"Content-Type":type}});
describe("Feedback transport and privacy", () => {
 beforeEach(() => vi.stubEnv("VITE_MINDCORE_FEEDBACK_ENDPOINT", ""));
 afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
 it("uses the shared schema and current product version", () => { expect(payload()).toEqual({schemaVersion:1,category:"bug",title:"제목",message:"내용",platform:"desktop",appVersion:"0.4.0"}); for (const [label,value] of [["기능 제안","feature"],["사용성/UI","usability"],["기타","other"]]) expect(feedbackPayload(label,"x","y",false,{}).category).toBe(value); });
 it("includes no diagnostics by default; allowlists names AND values", () => {
  const diagnostics = {platform:"desktop", app_version:"0.4.0", os_version:"15.7",provider:"groq",persona_connection:"BOUND_MATCH", update_state:"idle", API_KEY:"synthetic-private", DATABASE_URL:"libsql://private", identity:"private", architecture:"/Users/private", backend_status:"private"};
  expect(feedbackPayload("버그","제목","내용",false,diagnostics).diagnostics).toBeUndefined();
  expect(feedbackPayload("버그","제목","내용",true,diagnostics).diagnostics).toEqual({osVersion:"15.7",provider:"groq",personaConnection:"BOUND_MATCH",updaterState:"idle"});
  expect(JSON.stringify(feedbackPayload("기타","x","y",true,{provider:"synthetic-secret",os_version:"https://private?token=x",persona_connection:"private",update_state:"private"}))).not.toMatch(/private|secret|token/);
 });
 it("falls back only when unset and reports browser rather than sent", async () => {
  const open=vi.fn().mockResolvedValue(undefined); const fetch=vi.fn(); vi.stubGlobal("fetch",fetch); expect(feedbackEndpoint()).toBeNull(); expect(await submitFeedback(payload(),null,open)).toBe("browser"); expect(new URL(feedbackComposeUrl(payload())).searchParams.get("body")).toContain("내용"); expect(open).toHaveBeenCalledOnce(); expect(fetch).not.toHaveBeenCalled();
  open.mockRejectedValue(new Error("unavailable")); await expect(submitFeedback(payload(),null,open)).rejects.toThrow();
 });
 it("configures the endpoint through a public build variable", () => { expect(feedbackEndpoint()).toBeNull(); vi.stubEnv("VITE_MINDCORE_FEEDBACK_ENDPOINT","https://example.com/feedback"); expect(feedbackEndpoint()).toBe("https://example.com/feedback"); });
 it.each([200,202])("POSTs correct bounded JSON without auth and accepts %s + ok true", async status => {
  const fetch=vi.fn().mockResolvedValue(response('{"ok":true,"feedbackId":"test_1"}',status)); vi.stubGlobal("fetch",fetch);
  expect(await submitFeedback(payload(),"https://example.com/feedback")).toBe("sent"); const request=fetch.mock.calls[0][1]; expect(request.credentials).toBe("omit"); expect(request.redirect).toBe("error"); expect(request.method).toBe("POST"); expect(request.headers).toEqual({"Content-Type":"application/json"}); expect(JSON.parse(request.body)).toEqual(payload()); expect(request.signal).toBeInstanceOf(AbortSignal);
 });
 it.each([
  [200,'{"ok":false}',"application/json"],[200,'{"ok":"true"}',"application/json"],
  [200,'{}',"application/json"],[200,'<html>ok</html>',"text/html"],
  [200,'not-json',"application/json"],[200,'{ok:true}',"application/json"],[200,'{"ok":true,"ok":true}',"application/json"],[200,'{\f"ok":true}',"application/json"],[201,'{"ok":true}',"application/json"],
  [302,'{"ok":true}',"application/json"],[500,'{"ok":true}',"application/json"],
  [200,'{"ok":true,"feedbackId":"unsafe/secret"}',"application/json"],
  [200,'{"ok":true,"extra":"'+"x".repeat(4096)+'"}',"application/json"]
 ])("rejects response %s/%s without opening fallback", async (status,body,type) => {
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue(response(body as string,status as number,type as string))); const open=vi.fn(); const draft=payload(); const snapshot=JSON.stringify(draft);
  await expect(submitFeedback(draft,"https://example.com/feedback",open)).rejects.toThrow("피드백을 전송하지 못했습니다."); expect(open).not.toHaveBeenCalled(); expect(JSON.stringify(draft)).toBe(snapshot);
 });
 it.each([new TypeError("network-private"),new DOMException("timeout","TimeoutError")])("allows retry after network/timeout without mutating draft", async failure => {
  const draft=payload(); const fetch=vi.fn().mockRejectedValueOnce(failure).mockResolvedValueOnce(response('{"ok":true}')); vi.stubGlobal("fetch",fetch);
  await expect(submitFeedback(draft,"https://example.com/feedback")).rejects.toThrow("피드백을 전송하지 못했습니다."); expect(draft).toEqual(payload()); expect(await submitFeedback(draft,"https://example.com/feedback")).toBe("sent");
 });
 it("bounds inputs and validates again at the transport boundary", async () => {
  for (const args of [["unknown","x","y"],["버그"," ","y"],["버그","x".repeat(161),"y"],["버그","x","y".repeat(6001)]]) expect(()=>feedbackPayload(args[0],args[1],args[2],false,{})).toThrow();
  const fetch=vi.fn(); vi.stubGlobal("fetch",fetch); await expect(submitFeedback({...payload(),diagnostics:{provider:"secret"}},"https://example.com/feedback")).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled();
 });
 it("rejects malformed, local, credentialed and query-bearing endpoint URLs", async () => {
  for (const url of ["http://example.com","https://user:secret@example.com","https://localhost/","https://127.0.0.1/","https://[::1]/","https://private.local/","https://example.com/?%74oken=x","https://example.com/#secret","garbage"]) { expect(validFeedbackEndpoint(url)).toBe(false); await expect(submitFeedback(payload(),url)).rejects.toThrow(); }
  expect(validFeedbackEndpoint("https://example.com/feedback")).toBe(true);
 });
 it("keeps generic public link validation backward compatible", () => { for (const url of ["javascript:alert(1)","file:///tmp/secret","data:text/html,x","mindcore://secret","https://user:secret@example.com"]) expect(safePublicUrl(url)).toBe(false); expect(safePublicUrl("https://example.com")).toBe(true); expect(safePublicUrl("http://example.com")).toBe(true); });
});

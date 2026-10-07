import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
const apiMock = vi.hoisted(() => ({ health: vi.fn(), me: vi.fn(), listConversations: vi.fn(), createConversation: vi.fn(), listMessages: vi.fn(), login: vi.fn(), logout: vi.fn() }));
const invoke = vi.hoisted(() => vi.fn());
vi.mock("./api/client", () => ({ api: apiMock, ApiError: class ApiError extends Error {}, setAuthFailureHandler: vi.fn(), storeDesktopSession: vi.fn(), isDesktopRuntime: () => true }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./components/ChatWindow", () => ({ ChatWindow: ({ draft, onDraftChange }: {draft:string; onDraftChange:(value:string)=>void}) => <input aria-label="Chat draft" value={draft} onChange={event=>onDraftChange(event.target.value)} /> }));
vi.mock("./components/WorkspacePanel", () => ({ WorkspacePanel: ({view}:{view:string}) => <div>Domain: {view}</div> }));
vi.mock("./components/DesktopUpdater", () => ({ DesktopUpdater: () => <div>Updater support</div> }));
import App from "./App";
describe("native app navigation continuity", () => {
 beforeEach(() => {
  vi.clearAllMocks(); window.localStorage.clear();
  apiMock.health.mockResolvedValue({status:"ok"}); apiMock.me.mockResolvedValue({authenticated:true}); apiMock.listConversations.mockResolvedValue([{id:"existing"}]);
  invoke.mockImplementation((command:string) => Promise.resolve(command === "get_setup_status" ? {configured:true} : command === "list_personas" ? [] : command === "get_desktop_session" ? "synthetic-session" : undefined));
 });
 it("keeps Persona and configuration controls in app settings, and opens top-level Feedback", async () => {
  render(<App/>); await screen.findByLabelText("Chat draft");
  expect(within(screen.getByRole("navigation",{name:"MindCore workspace"})).getAllByRole("button").map(button=>button.textContent)).toEqual(["대화","데이터 관리","앱 설정","피드백"]);
  expect(screen.queryByRole("button",{name:"Persona 추가"})).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"앱 설정"}));
  for(const label of ["Persona 추가","Persona 관리","Identity 파일 열기","현재 Persona 재설정"]) expect(screen.getByRole("button",{name:label})).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"연결"})); expect(await screen.findByRole("heading",{name:"Persona 연결"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"피드백"})); expect(await screen.findByRole("dialog",{name:"피드백"})).toBeTruthy();
 });
 it("preserves chat draft, selected conversation and data tab across product sections", async () => {
  render(<App/>); const draft=await screen.findByLabelText("Chat draft"); fireEvent.change(draft,{target:{value:"synthetic unsent draft"}});
  fireEvent.click(screen.getByRole("button",{name:"데이터 관리"})); fireEvent.click(screen.getByRole("button",{name:"기억"})); fireEvent.click(screen.getByRole("button",{name:"에피소드"})); expect(screen.getByText("Domain: episodes")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"대화"})); expect((await screen.findByLabelText("Chat draft") as HTMLInputElement).value).toBe("synthetic unsent draft");
  fireEvent.click(screen.getByRole("button",{name:"데이터 관리"})); expect(screen.getByRole("button",{name:"에피소드"}).getAttribute("aria-current")).toBe("page"); expect(apiMock.createConversation).not.toHaveBeenCalled();
 });
});

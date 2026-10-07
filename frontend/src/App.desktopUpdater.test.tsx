import { fireEvent, render, screen } from "@testing-library/react";
import { createPortal } from "react-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ health: vi.fn(), me: vi.fn(), listConversations: vi.fn(), createConversation: vi.fn(), listMessages: vi.fn(), login: vi.fn(), logout: vi.fn() }));
const invoke = vi.hoisted(() => vi.fn());
const updaterRender = vi.hoisted(() => vi.fn());

vi.mock("./api/client", () => ({ api: apiMock, ApiError: class ApiError extends Error {}, setAuthFailureHandler: vi.fn(), storeDesktopSession: vi.fn(), isDesktopRuntime: () => true }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./components/SetupWizard", () => ({ SetupWizard: () => <div>Setup Wizard</div> }));
vi.mock("./components/Sidebar", () => ({ Sidebar: ({onSectionChange}:{onSectionChange:(value:string)=>void}) => <><button onClick={()=>onSectionChange("settings")}>앱 설정</button><button onClick={()=>onSectionChange("feedback")}>피드백</button></> }));
vi.mock("./components/ChatWindow", () => ({ ChatWindow: () => <div>Main Chat</div> }));
vi.mock("./components/DesktopUpdater", () => ({
  DesktopUpdater: ({ enabled, controlsTarget }: {enabled?: boolean; controlsTarget?: HTMLElement | null}) => {
    updaterRender();
    const controls = <div><span>버전 0.4.0</span><button>업데이트 내용</button>{enabled && <button type="button">업데이트 확인</button>}</div>;
    return controlsTarget === undefined ? controls : controlsTarget ? createPortal(controls, controlsTarget) : null;
  },
}));

import App from "./App";

describe("desktop product support across setup and workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.health.mockResolvedValue({ status: "ok" });
    apiMock.me.mockResolvedValue({ authenticated: true });
    apiMock.listConversations.mockResolvedValue([]);
    apiMock.createConversation.mockResolvedValue({ id: "main" });
  });

  it("renders feedback and update controls in setup without a runtime", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: false });
      if (command === "get_runtime_capabilities") return Promise.resolve({ updater_available: true });
      return Promise.resolve();
    });
    render(<App />);
    expect(await screen.findByText("Setup Wizard")).toBeTruthy();
    expect(screen.getByRole("button", { name: "업데이트 확인" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "피드백" })).toBeTruthy();
  });

  it("renders updater UI when the native build reports updater availability", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: true });
      if (command === "get_runtime_capabilities") return Promise.resolve({ updater_available: true });
      if (command === "get_desktop_session") return Promise.resolve("desktop-session");
      if (command === "list_personas") return Promise.resolve([]);
      return Promise.resolve();
    });
    render(<App />);
    await screen.findByText("Main Chat"); fireEvent.click(screen.getByRole("button",{name:"앱 설정"})); fireEvent.click(screen.getByRole("button",{name:"업데이트"}));
    expect(await screen.findByRole("button", { name: "업데이트 확인" })).toBeTruthy();
    expect(updaterRender).toHaveBeenCalled();
  });

  it("retains current notes and feedback without native update actions in a disabled build", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: true });
      if (command === "get_runtime_capabilities") return Promise.resolve({ updater_available: false });
      if (command === "get_desktop_session") return Promise.resolve("desktop-session");
      if (command === "list_personas") return Promise.resolve([]);
      return Promise.resolve();
    });
    render(<App />);
    expect(await screen.findByText("Main Chat")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "업데이트 확인" })).toBeNull();
    expect(screen.queryByRole("button",{name:"업데이트 내용"})).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"앱 설정"})); fireEvent.click(screen.getByRole("button",{name:"업데이트"}));
    expect(screen.getByRole("button", {name:"업데이트 내용"})).toBeTruthy();
    expect(screen.getByRole("button", {name:"피드백"})).toBeTruthy();
  });
});

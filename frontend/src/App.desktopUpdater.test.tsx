import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ health: vi.fn(), me: vi.fn(), listConversations: vi.fn(), createConversation: vi.fn(), listMessages: vi.fn(), login: vi.fn(), logout: vi.fn() }));
const invoke = vi.hoisted(() => vi.fn());
const updaterRender = vi.hoisted(() => vi.fn());

vi.mock("./api/client", () => ({ api: apiMock, ApiError: class ApiError extends Error {}, setAuthFailureHandler: vi.fn(), storeDesktopSession: vi.fn(), isDesktopRuntime: () => true }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./components/SetupWizard", () => ({ SetupWizard: () => <div>Setup Wizard</div> }));
vi.mock("./components/Sidebar", () => ({ Sidebar: () => <div>Sidebar</div> }));
vi.mock("./components/ChatWindow", () => ({ ChatWindow: () => <div>Main Chat</div> }));
vi.mock("./components/DesktopUpdater", () => ({
  DesktopUpdater: ({ enabled }: {enabled?: boolean}) => {
    updaterRender();
    return <div><span>버전 0.4.0</span><button>업데이트 내용</button>{enabled && <button type="button">업데이트 확인</button>}</div>;
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
    expect(screen.getByRole("button", { name: "Feedback" })).toBeTruthy();
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
    expect(screen.getByRole("button", {name:"업데이트 내용"})).toBeTruthy();
    expect(screen.getByRole("button", {name:"Feedback"})).toBeTruthy();
  });
});

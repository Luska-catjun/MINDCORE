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
  DesktopUpdater: () => {
    updaterRender();
    return <button type="button">Check for Updates</button>;
  },
}));

import App from "./App";

describe("desktop updater first-run suppression", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.health.mockResolvedValue({ status: "ok" });
    apiMock.me.mockResolvedValue({ authenticated: true });
    apiMock.listConversations.mockResolvedValue([]);
    apiMock.createConversation.mockResolvedValue({ id: "main" });
  });

  it("does not render updater UI while Setup Wizard is active", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: false });
      if (command === "get_runtime_capabilities") return Promise.resolve({ updater_available: true });
      return Promise.resolve();
    });
    render(<App />);
    expect(await screen.findByText("Setup Wizard")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check for Updates" })).toBeNull();
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
    expect(await screen.findByRole("button", { name: "Check for Updates" })).toBeTruthy();
    expect(updaterRender).toHaveBeenCalled();
  });

  it("does not render or invoke updater UI in an updater-disabled build", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: true });
      if (command === "get_runtime_capabilities") return Promise.resolve({ updater_available: false });
      if (command === "get_desktop_session") return Promise.resolve("desktop-session");
      if (command === "list_personas") return Promise.resolve([]);
      return Promise.resolve();
    });
    render(<App />);
    expect(await screen.findByText("Main Chat")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check for Updates" })).toBeNull();
    expect(updaterRender).not.toHaveBeenCalled();
  });
});

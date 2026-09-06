import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  health: vi.fn(),
  me: vi.fn(),
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  listMessages: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
}));
const invoke = vi.hoisted(() => vi.fn());

vi.mock("./api/client", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  setAuthFailureHandler: vi.fn(),
  storeDesktopSession: vi.fn(),
  isDesktopRuntime: () => true,
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./components/Sidebar", () => ({ Sidebar: () => <div>Sidebar</div> }));
vi.mock("./components/ChatWindow", () => ({ ChatWindow: () => <div>Main Chat</div> }));
vi.mock("./components/DesktopUpdater", () => ({ DesktopUpdater: () => null }));

import App from "./App";

describe("desktop backend lifecycle recovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("Retry invokes native start again after the managed child fails", async () => {
    let startCalls = 0;
    let backendAvailable = false;
    let clock = 0;
    vi.spyOn(Date, "now").mockImplementation(() => {
      clock += 31_000;
      return clock;
    });
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: true });
      if (command === "start_mindcore_backend") {
        startCalls += 1;
        if (startCalls === 1) return Promise.reject(new Error("managed child exited"));
        backendAvailable = true;
        return Promise.resolve();
      }
      if (command === "get_desktop_session") return Promise.resolve("desktop-session");
      return Promise.resolve();
    });
    apiMock.health.mockImplementation(() => backendAvailable
      ? Promise.resolve({ status: "ok" })
      : Promise.reject(new Error("backend unavailable")));
    apiMock.me.mockResolvedValue({ authenticated: true });
    apiMock.listConversations.mockResolvedValue([]);
    apiMock.createConversation.mockResolvedValue({ id: "main" });

    render(<App />);
    const retry = await screen.findByRole("button", { name: "Retry" });
    await userEvent.click(retry);

    await waitFor(() => expect(startCalls).toBe(2));
    expect(await screen.findByText("Main Chat")).toBeTruthy();
  });

  it("Restart MindCore invokes native start before retrying the session", async () => {
    let sessionCalls = 0;
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: true });
      if (command === "start_mindcore_backend") return Promise.resolve();
      if (command === "get_desktop_session") {
        sessionCalls += 1;
        return sessionCalls === 1
          ? Promise.reject(new Error("session unavailable"))
          : Promise.resolve("desktop-session");
      }
      return Promise.resolve();
    });
    apiMock.health.mockResolvedValue({ status: "ok" });
    apiMock.me.mockResolvedValue({ authenticated: true });
    apiMock.listConversations.mockResolvedValue([]);
    apiMock.createConversation.mockResolvedValue({ id: "main" });

    render(<App />);
    const restart = await screen.findByRole("button", { name: "Restart MindCore" });
    await userEvent.click(restart);

    await waitFor(() => {
      expect(invoke.mock.calls.filter(([command]) => command === "start_mindcore_backend")).toHaveLength(2);
    });
    expect(await screen.findByText("Main Chat")).toBeTruthy();
  });
});

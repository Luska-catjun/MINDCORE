import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ health: vi.fn(), me: vi.fn(), listConversations: vi.fn(), createConversation: vi.fn(), listMessages: vi.fn(), login: vi.fn(), logout: vi.fn() }));
const invoke = vi.hoisted(() => vi.fn());
const storeDesktopSession = vi.hoisted(() => vi.fn());

vi.mock("./api/client", () => ({ api: apiMock, ApiError: class ApiError extends Error {}, setAuthFailureHandler: vi.fn(), storeDesktopSession, isDesktopRuntime: () => true }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./components/Sidebar", () => ({ Sidebar: () => <div>Sidebar</div> }));
vi.mock("./components/ChatWindow", () => ({ ChatWindow: () => <div>Main Chat</div> }));
vi.mock("./components/DesktopUpdater", () => ({ DesktopUpdater: () => null }));

import App from "./App";

describe("desktop authentication", () => {
  it("uses a native session and never renders the password login", async () => {
    invoke.mockImplementation((command: string) => command === "get_setup_status"
      ? Promise.resolve({ configured: true })
      : Promise.resolve("desktop-session"));
    apiMock.health.mockResolvedValue({ status: "ok" });
    apiMock.me.mockResolvedValue({ authenticated: true });
    apiMock.listConversations.mockResolvedValue([]);
    apiMock.createConversation.mockResolvedValue({ id: "main" });

    render(<App />);
    expect(await screen.findByText("Main Chat")).toBeTruthy();
    expect(storeDesktopSession).toHaveBeenCalledWith("desktop-session");
    expect(screen.queryByLabelText("Private access password")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });

  it("shows a local-session startup error instead of the password login", async () => {
    invoke.mockImplementation((command: string) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: true });
      if (command === "start_mindcore_backend") return Promise.resolve();
      return Promise.reject(new Error("native session unavailable"));
    });
    apiMock.health.mockResolvedValue({ status: "ok" });

    render(<App />);
    expect(await screen.findByText("MindCore could not start the local session.")).toBeTruthy();
    expect(screen.queryByLabelText("Private access password")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });
});

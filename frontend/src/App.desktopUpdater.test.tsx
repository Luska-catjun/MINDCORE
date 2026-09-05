import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ health: vi.fn(), me: vi.fn(), listConversations: vi.fn(), createConversation: vi.fn(), listMessages: vi.fn(), login: vi.fn(), logout: vi.fn() }));
const invoke = vi.hoisted(() => vi.fn());

vi.mock("./api/client", () => ({ api: apiMock, ApiError: class ApiError extends Error {}, setAuthFailureHandler: vi.fn(), storeDesktopSession: vi.fn(), isDesktopRuntime: () => true }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./components/SetupWizard", () => ({ SetupWizard: () => <div>Setup Wizard</div> }));

import App from "./App";

describe("desktop updater first-run suppression", () => {
  it("does not render updater UI while Setup Wizard is active", async () => {
    invoke.mockResolvedValueOnce({ configured: false });
    render(<App />);
    expect(await screen.findByText("Setup Wizard")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check for Updates" })).toBeNull();
  });
});

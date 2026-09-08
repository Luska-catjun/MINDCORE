import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const personas = [
  { persona_id: "persona-a", display_name: "Jarvis", created_at: 1, last_used_at: 1, active: true, avatar_extension: "png" },
  { persona_id: "persona-b", display_name: "Nova", created_at: 2, last_used_at: null, active: false, avatar_extension: null },
];
const invoke = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({ health: vi.fn(), me: vi.fn(), listConversations: vi.fn(), createConversation: vi.fn(), listMessages: vi.fn(), login: vi.fn(), logout: vi.fn() }));
const chatHarness = vi.hoisted(() => ({ props: null as null | Record<string, any>, send: null as any }));
let active = personas[0];
let rejectSwitch = false;

vi.mock("./api/client", () => ({ api: apiMock, ApiError: class ApiError extends Error {}, setAuthFailureHandler: vi.fn(), storeDesktopSession: vi.fn(), isDesktopRuntime: () => true }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./components/Sidebar", () => ({ Sidebar: () => <div>Sidebar</div> }));
vi.mock("./components/WorkspacePanel", () => ({ WorkspacePanel: () => <div>Observation</div> }));
vi.mock("./components/DesktopUpdater", () => ({ DesktopUpdater: () => null }));
vi.mock("./components/PersonaManager", () => ({ PersonaManager: () => null }));
vi.mock("./components/ChatWindow", () => ({
  ChatWindow: (props: Record<string, any>) => {
    chatHarness.props = props;
    return <div>
      <span>{props.personaDisplayName}</span>
      <span data-testid="user-display-name">{props.userDisplayName}</span>
      <span data-testid="message-count">{props.messages.length}</span>
      <button type="button" onClick={() => {
        chatHarness.send = props.onSendStarted(props.conversationId, {
          id: "temp-a", conversation_id: props.conversationId, role: "user", content: "A pending", sequence: 1, timestamp: "2026-01-01T00:00:00Z", created_at: "2026-01-01T00:00:00Z", source_device: "test", metadata: {}, _pending: true,
        });
      }}>Start A request</button>
    </div>;
  },
}));

import App from "./App";

describe("multi-Persona desktop isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chatHarness.props = null;
    chatHarness.send = null;
    active = personas[0];
    rejectSwitch = false;
    invoke.mockImplementation((command: string, args?: { personaId?: string }) => {
      if (command === "get_setup_status") return Promise.resolve({ configured: true });
      if (command === "list_personas") return Promise.resolve(personas.map((persona) => ({ ...persona, active: persona.persona_id === active.persona_id })));
      if (command === "switch_active_persona") {
        if (rejectSwitch) return Promise.reject(new Error("switch failed"));
        active = personas.find((persona) => persona.persona_id === args?.personaId) ?? active;
        return Promise.resolve({ ...active, active: true });
      }
      if (command === "get_desktop_session") return Promise.resolve("desktop-session");
      return Promise.resolve();
    });
    apiMock.health.mockResolvedValue({ status: "ok" });
    apiMock.me.mockImplementation(() => Promise.resolve({ authenticated: true, persona_id: active.persona_id, persona_display_name: active.display_name, user_display_name: "Luska" }));
    apiMock.listConversations.mockResolvedValue([]);
    apiMock.createConversation.mockImplementation(() => Promise.resolve({ id: `conversation-${active.persona_id}` }));
  });

  it("clears A state before switching and rejects A's late response in B", async () => {
    render(<App />);
    await waitFor(() => expect(
      (screen.getByLabelText("Current Persona") as HTMLSelectElement).value,
    ).toBe("persona-a"));
    await userEvent.click(screen.getByRole("button", { name: "Start A request" }));
    expect(screen.getByTestId("message-count").textContent).toBe("1");

    await userEvent.selectOptions(screen.getByLabelText("Current Persona"), "persona-b");
    await waitFor(() => expect(
      (screen.getByLabelText("Current Persona") as HTMLSelectElement).value,
    ).toBe("persona-b"));
    await waitFor(() => expect(screen.getByTestId("message-count").textContent).toBe("0"));

    let accepted = true;
    act(() => {
      accepted = chatHarness.props?.onSendSucceeded(chatHarness.send, {
        user_message: { id: "a-user", conversation_id: "conversation-persona-a", role: "user", content: "A", sequence: 1, timestamp: "2026-01-01T00:00:00Z", created_at: "2026-01-01T00:00:00Z", source_device: "test", metadata: {} },
        diana_message: { id: "a-assistant", conversation_id: "conversation-persona-a", role: "diana", content: "A reply", sequence: 2, timestamp: "2026-01-01T00:00:01Z", created_at: "2026-01-01T00:00:01Z", source_device: "test", metadata: {} },
      });
    });
    expect(accepted).toBe(false);
    expect(screen.getByTestId("message-count").textContent).toBe("0");
    expect(invoke).toHaveBeenCalledWith("switch_active_persona", { personaId: "persona-b" });
  });

  it("keeps the previous Persona active when native switching fails", async () => {
    render(<App />);
    await waitFor(() => expect(
      (screen.getByLabelText("Current Persona") as HTMLSelectElement).value,
    ).toBe("persona-a"));
    rejectSwitch = true;

    await userEvent.selectOptions(screen.getByLabelText("Current Persona"), "persona-b");

    await waitFor(() => expect(
      (screen.getByLabelText("Current Persona") as HTMLSelectElement).value,
    ).toBe("persona-a"));
    expect(screen.getByText("Persona switch failed. The previous Persona remains active.")).toBeTruthy();
  });

  it("keeps the global user display name while switching Personas", async () => {
    render(<App />);
    await waitFor(() => expect(chatHarness.props?.personaDisplayName).toBe("Jarvis"));
    expect(screen.getByTestId("user-display-name").textContent).toBe("Luska");

    await userEvent.selectOptions(screen.getByLabelText("Current Persona"), "persona-b");
    await waitFor(() => expect(chatHarness.props?.personaDisplayName).toBe("Nova"));
    expect(screen.getByTestId("user-display-name").textContent).toBe("Luska");

    await userEvent.selectOptions(screen.getByLabelText("Current Persona"), "persona-a");
    await waitFor(() => expect(chatHarness.props?.personaDisplayName).toBe("Jarvis"));
    expect(screen.getByTestId("user-display-name").textContent).toBe("Luska");
  });

  it("discards a late conversation lookup from the previous Persona", async () => {
    let resolveA: ((value: Array<{ id: string }>) => void) | undefined;
    apiMock.listConversations
      .mockImplementationOnce(() => new Promise((resolve) => { resolveA = resolve; }))
      .mockResolvedValueOnce([{ id: "conversation-persona-b" }]);
    render(<App />);
    await waitFor(() => expect(
      (screen.getByLabelText("Current Persona") as HTMLSelectElement).value,
    ).toBe("persona-a"));

    await userEvent.selectOptions(screen.getByLabelText("Current Persona"), "persona-b");
    await waitFor(() => expect(chatHarness.props?.conversationId).toBe("conversation-persona-b"));
    resolveA?.([{ id: "conversation-persona-a" }]);
    await Promise.resolve();

    expect(chatHarness.props?.conversationId).toBe("conversation-persona-b");
  });
});

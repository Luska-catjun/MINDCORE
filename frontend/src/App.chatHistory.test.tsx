import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatResponse, ConversationRead, MessageRead } from "./types/api";

const apiMock = vi.hoisted(() => ({
  health: vi.fn(),
  me: vi.fn(),
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  listMessages: vi.fn(),
  sendChatMessage: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("./api/client", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {
    status = 500;
  },
  setAuthFailureHandler: vi.fn(),
  isDesktopRuntime: () => false,
}));

vi.mock("./components/WorkspacePanel", () => ({
  WorkspacePanel: ({ view }: { view: string }) => <div data-testid="workspace">Observation: {view}</div>,
}));

import App from "./App";

const conversation = (id = "conversation-x"): ConversationRead => ({
  id,
  title: "Diana",
  source_device: "test",
  status: "active",
  metadata: {},
  started_at: "2026-01-01T00:00:00Z",
  last_message_at: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

const message = (id: string, content: string, conversationId = "conversation-x", role: "user" | "diana" = "user"): MessageRead => ({
  id,
  conversation_id: conversationId,
  role,
  content,
  source_device: "test",
  sequence: null,
  metadata: {},
  timestamp: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
});

describe("Chat history across Observation views", () => {
  let durableMessages: MessageRead[];

  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    durableMessages = [message("a", "A"), message("b", "B", "conversation-x", "diana")];
    apiMock.health.mockResolvedValue({ status: "ok" });
    apiMock.me.mockResolvedValue({});
    apiMock.listConversations.mockResolvedValue([conversation()]);
    apiMock.listMessages.mockImplementation(async () => durableMessages);
    apiMock.sendChatMessage.mockImplementation(async ({ content }: { content: string }): Promise<ChatResponse> => {
      const user = message("c", content);
      const diana = message("d", "Diana reply", "conversation-x", "diana");
      durableMessages = [...durableMessages, user, diana];
      return { user_message: user, diana_message: diana };
    });
  });

  async function renderReadyApp() {
    render(<App />);
    await screen.findByText("A");
    await screen.findByText("B");
  }

  it("restores durable history after Chat → Observation → Chat without resetting the selected conversation", async () => {
    await renderReadyApp();

    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(screen.getByTestId("workspace").textContent).toContain("Observation: memory");

    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await screen.findByText("A");
    expect(screen.getByText("B")).toBeTruthy();
    expect(apiMock.listMessages).toHaveBeenLastCalledWith("conversation-x", 200, 0, true);
    expect(durableMessages.map((item) => item.content)).toEqual(["A", "B"]);
  });

  it("does not expose desktop updater controls in web mode", async () => {
    await renderReadyApp();
    expect(screen.queryByRole("button", { name: "Check for Updates" })).toBeNull();
  });

  it("keeps a newly sent message through the same round trip", async () => {
    await renderReadyApp();
    const input = screen.getByPlaceholderText("메시지를 입력하세요...");
    fireEvent.change(input, { target: { value: "C" } });
    await userEvent.click(screen.getByRole("button", { name: "전송" }));
    await screen.findByText("Diana reply");

    await userEvent.click(screen.getByRole("button", { name: "Episodes" }));
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));

    await screen.findByText("C");
    expect(screen.getByText("Diana reply")).toBeTruthy();
    expect(durableMessages.map((item) => item.content)).toEqual(["A", "B", "C", "Diana reply"]);
  });

  it("re-fetches the same selected conversation after a real Chat unmount/remount", async () => {
    await renderReadyApp();
    await userEvent.click(screen.getByRole("button", { name: "Emotion" }));
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));

    await waitFor(() => expect(apiMock.listMessages).toHaveBeenCalledTimes(2));
    expect(apiMock.listMessages.mock.calls).toEqual([
      ["conversation-x", 200, 0, true],
      ["conversation-x", 200, 0, true],
    ]);
  });

  it("keeps new messages after remounting a 200-message latest window", async () => {
    durableMessages = Array.from({ length: 200 }, (_, index) => ({
      ...message(`message-${index + 3}`, `M${index + 3}`, "conversation-x", index % 2 ? "diana" : "user"),
      sequence: index + 3,
    }));

    render(<App />);
    await screen.findByText("M3");
    await screen.findByText("M202");
    expect(screen.queryByText("M1")).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("메시지를 입력하세요..."), { target: { value: "C" } });
    await userEvent.click(screen.getByRole("button", { name: "전송" }));
    await screen.findByText("Diana reply");

    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await screen.findByText("C");
    expect(screen.getByText("Diana reply")).toBeTruthy();
    expect(apiMock.listMessages).toHaveBeenLastCalledWith("conversation-x", 200, 0, true);
  });
});

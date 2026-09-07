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
  WorkspacePanel: ({
    view,
    onMessageDeleted,
  }: {
    view: string;
    onMessageDeleted?: (conversationId: string, messageId: string) => void;
  }) => (
    <div data-testid="workspace">
      Observation: {view}
      {view === "messages" ? (
        <button type="button" onClick={() => onMessageDeleted?.("conversation-x", "b")}>
          Simulate durable message deletion
        </button>
      ) : null}
    </div>
  ),
}));

import App from "./App";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

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
    window.__DIANA_CHAT_DEBUG__ = undefined;
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

  it("merges a pending response after remount replaced its optimistic anchor", async () => {
    const remountFetch = deferred<MessageRead[]>();
    const send = deferred<ChatResponse>();
    const durableUser = message("c", "C");
    const durableAssistant = message("d", "D", "conversation-x", "diana");
    apiMock.listMessages
      .mockResolvedValueOnce(durableMessages)
      .mockImplementationOnce(() => remountFetch.promise);
    apiMock.sendChatMessage.mockImplementationOnce(() => send.promise);
    await renderReadyApp();

    fireEvent.change(screen.getByPlaceholderText("메시지를 입력하세요..."), { target: { value: "C" } });
    await userEvent.click(screen.getByRole("button", { name: "전송" }));
    expect(screen.getByText("C")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await waitFor(() => expect(apiMock.listMessages).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/가 생각 중\.\.\.$/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "전송 중..." }) as HTMLButtonElement).disabled).toBe(true);
    remountFetch.resolve([...durableMessages, durableUser]);
    await screen.findByText("C");

    send.resolve({ user_message: durableUser, diana_message: durableAssistant });
    await screen.findByText("D");
    expect(screen.getAllByText("C")).toHaveLength(1);
    expect(screen.getAllByText("D")).toHaveLength(1);
  });

  it.each([
    ["A,B", (base: MessageRead[], _user: MessageRead, _assistant: MessageRead) => base],
    ["A,B,C,D", (base: MessageRead[], user: MessageRead, assistant: MessageRead) => [...base, user, assistant]],
  ])("reconciles when remount GET returns %s before POST", async (_case, remountRows) => {
    const remountFetch = deferred<MessageRead[]>();
    const send = deferred<ChatResponse>();
    const durableUser = message("c", "C");
    const durableAssistant = message("d", "D", "conversation-x", "diana");
    apiMock.listMessages
      .mockResolvedValueOnce(durableMessages)
      .mockImplementationOnce(() => remountFetch.promise);
    apiMock.sendChatMessage.mockImplementationOnce(() => send.promise);
    await renderReadyApp();

    fireEvent.change(screen.getByPlaceholderText("메시지를 입력하세요..."), { target: { value: "C" } });
    await userEvent.click(screen.getByRole("button", { name: "전송" }));
    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await waitFor(() => expect(apiMock.listMessages).toHaveBeenCalledTimes(2));

    remountFetch.resolve(remountRows(durableMessages, durableUser, durableAssistant));
    await waitFor(() => expect(window.__DIANA_CHAT_DEBUG__?.lastFetch?.status).toBe("applied"));
    send.resolve({ user_message: durableUser, diana_message: durableAssistant });

    await screen.findByText("D");
    expect(screen.getAllByText("C")).toHaveLength(1);
    expect(screen.getAllByText("D")).toHaveLength(1);
  });

  it("does not duplicate durable rows when POST resolves before remount GET", async () => {
    const send = deferred<ChatResponse>();
    const durableUser = message("c", "C");
    const durableAssistant = message("d", "D", "conversation-x", "diana");
    apiMock.sendChatMessage.mockImplementationOnce(() => send.promise);
    await renderReadyApp();

    fireEvent.change(screen.getByPlaceholderText("메시지를 입력하세요..."), { target: { value: "C" } });
    await userEvent.click(screen.getByRole("button", { name: "전송" }));
    await userEvent.click(screen.getByRole("button", { name: "Memory" }));
    durableMessages = [...durableMessages, durableUser, durableAssistant];
    send.resolve({ user_message: durableUser, diana_message: durableAssistant });
    await waitFor(() => expect(window.__DIANA_CHAT_DEBUG__?.cache?.map((item) => item.id)).toEqual(["a", "b", "c", "d"]));

    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await screen.findByText("D");
    expect(screen.getAllByText("C")).toHaveLength(1);
    expect(screen.getAllByText("D")).toHaveLength(1);
  });

  it("discards a late POST response after logout resets the auth session", async () => {
    const send = deferred<ChatResponse>();
    apiMock.sendChatMessage.mockImplementationOnce(() => send.promise);
    await renderReadyApp();

    fireEvent.change(screen.getByPlaceholderText("메시지를 입력하세요..."), { target: { value: "C" } });
    await userEvent.click(screen.getByRole("button", { name: "전송" }));
    await userEvent.click(screen.getByRole("button", { name: "Log out" }));
    await screen.findByRole("button", { name: "Sign in" });

    send.resolve({
      user_message: message("c", "C"),
      diana_message: message("d", "D", "conversation-x", "diana"),
    });
    await waitFor(() => expect(window.__DIANA_CHAT_DEBUG__?.lastEvent).toBe("stale_send_discarded"));
    expect(screen.queryByText("C")).toBeNull();
    expect(screen.queryByText("D")).toBeNull();
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

  it("invalidates the canonical Chat cache after an Observation message deletion", async () => {
    await renderReadyApp();

    await userEvent.click(screen.getByRole("button", { name: "Messages" }));
    await userEvent.click(screen.getByRole("button", { name: "Simulate durable message deletion" }));
    durableMessages = durableMessages.filter((item) => item.id !== "b");
    expect(window.__DIANA_CHAT_DEBUG__?.cache?.map((item) => item.id)).toEqual(["a"]);

    await userEvent.click(screen.getByRole("button", { name: "Chat" }));
    await screen.findByText("A");
    expect(screen.queryByText("B")).toBeNull();
    await waitFor(() => expect(apiMock.listMessages).toHaveBeenCalledTimes(2));
    expect(window.__DIANA_CHAT_DEBUG__?.cache?.map((item) => item.id)).toEqual(["a"]);
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

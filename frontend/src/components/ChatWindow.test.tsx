import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
const listMessages = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("../api/client", () => ({
  api: { listMessages, sendChatMessage: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

import { ChatWindow } from "./ChatWindow";

const props = {
  personaDisplayName: "Jarvis",
  personaId: "persona-a",
  personaAvatarExtension: "png",
  conversationId: "conversation-a",
  loadingConversation: false,
  onToggleSidebar: vi.fn(),
  sourceDevice: "desktop",
  onStateUpdated: vi.fn(),
  messages: [],
  historyRevision: 0,
  sending: false,
  onDurableMessagesLoaded: vi.fn(),
  onSendStarted: vi.fn(),
  onSendSucceeded: vi.fn(),
  onSendFailed: vi.fn(),
};

describe("ChatWindow Persona avatar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let imageNumber = 0;
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => `blob:header-avatar-${++imageNumber}`) });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    invoke.mockResolvedValue({ mime_type: "image/png", bytes: [137, 80, 78, 71] });
    listMessages.mockResolvedValue([]);
  });

  it("refreshes the fixed-size header avatar when a same-extension replacement changes revision", async () => {
    const { rerender } = render(<ChatWindow {...props} personaAvatarRevision={1} />);
    const avatar = await screen.findByAltText("Jarvis avatar");
    await waitFor(() => expect(avatar.getAttribute("src")).toBe("blob:header-avatar-1"));
    expect(avatar.className).toContain("persona-avatar");
    expect(avatar.className).toContain("header-avatar");

    rerender(<ChatWindow {...props} personaAvatarRevision={2} />);
    await waitFor(() => expect(screen.getByAltText("Jarvis avatar").getAttribute("src")).toBe("blob:header-avatar-2"));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:header-avatar-1");
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { MessageBubble } from "./MessageBubble";

const message = (role: "diana" | "user" | "system" | "tool") => ({
  id: `${role}-message`,
  conversation_id: "conversation-a",
  role,
  content: "Test message",
  source_device: "desktop",
  sequence: 1,
  metadata: {},
  timestamp: "2026-09-07T10:00:00Z",
  created_at: "2026-09-07T10:00:00Z",
});

const avatarProps = {
  personaDisplayName: "Jarvis",
  userDisplayName: "Luska",
  personaId: "persona-a",
  personaAvatarExtension: "png",
  personaAvatarRevision: 1,
};

describe("MessageBubble Persona avatars", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let imageNumber = 0;
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => `blob:message-avatar-${++imageNumber}`) });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    invoke.mockResolvedValue({ mime_type: "image/png", bytes: [137, 80, 78, 71] });
  });

  it("renders the managed avatar for Persona messages and reloads same-extension replacements", async () => {
    const { rerender } = render(<MessageBubble message={message("diana")} {...avatarProps} />);
    const avatar = await screen.findByAltText("Jarvis avatar");
    await waitFor(() => expect(avatar.getAttribute("src")).toBe("blob:message-avatar-1"));
    expect(avatar.className).toContain("persona-avatar");
    expect(avatar.className).toContain("message-avatar");

    rerender(<MessageBubble message={message("diana")} {...avatarProps} personaAvatarRevision={2} />);
    await waitFor(() => expect(screen.getByAltText("Jarvis avatar").getAttribute("src")).toBe("blob:message-avatar-2"));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:message-avatar-1");
  });

  it("uses the Persona initial after avatar removal", () => {
    render(<MessageBubble message={message("diana")} {...avatarProps} personaAvatarExtension={null} />);
    expect(screen.getByLabelText("Jarvis avatar").textContent).toBe("J");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("uses the active Persona avatar after an A/B Persona switch", async () => {
    const { rerender } = render(<MessageBubble message={message("diana")} {...avatarProps} />);
    await screen.findByAltText("Jarvis avatar");

    rerender(<MessageBubble message={message("diana")} userDisplayName="Luska" personaDisplayName="Nova" personaId="persona-b" personaAvatarExtension="webp" personaAvatarRevision={1} />);
    await screen.findByAltText("Nova avatar");
    expect(invoke).toHaveBeenLastCalledWith("read_persona_avatar", { personaId: "persona-b" });
    expect(screen.queryByAltText("Jarvis avatar")).toBeNull();
  });

  it("does not use a Persona avatar for user, system, or tool messages", () => {
    const { rerender } = render(<MessageBubble message={message("user")} {...avatarProps} />);
    expect(screen.queryByLabelText("Jarvis avatar")).toBeNull();
    expect(screen.getByText("Luska")).toBeTruthy();
    expect(invoke).not.toHaveBeenCalled();

    rerender(<MessageBubble message={message("system")} {...avatarProps} />);
    expect(screen.queryByLabelText("Jarvis avatar")).toBeNull();
    expect(screen.getByLabelText("System").className).toContain("message-avatar-neutral");

    rerender(<MessageBubble message={message("tool")} {...avatarProps} />);
    expect(screen.queryByLabelText("Jarvis avatar")).toBeNull();
    expect(screen.getByLabelText("Tool").className).toContain("message-avatar-neutral");
    expect(invoke).not.toHaveBeenCalled();
  });
});

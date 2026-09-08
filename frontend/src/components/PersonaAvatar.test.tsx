import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { PersonaAvatar } from "./PersonaAvatar";

describe("PersonaAvatar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:persona-avatar") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  });

  it("uses a generic initial fallback when no avatar metadata exists", () => {
    render(<PersonaAvatar personaId="persona-a" displayName="Jarvis" avatarExtension={null} />);
    expect(screen.getByLabelText("Jarvis avatar").textContent).toBe("J");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("loads only the managed Persona avatar for a Persona with metadata", async () => {
    invoke.mockResolvedValue({ mime_type: "image/png", bytes: [137, 80, 78, 71] });
    render(<PersonaAvatar personaId="persona-a" displayName="Jarvis" avatarExtension="png" />);
    const image = await screen.findByAltText("Jarvis avatar");
    await waitFor(() => expect(image.getAttribute("src")).toBe("blob:persona-avatar"));
    expect(invoke).toHaveBeenCalledWith("read_persona_avatar", { personaId: "persona-a" });
    expect(image.className).toContain("persona-avatar");
  });

  it("revokes a failed image URL and returns to the initial fallback", async () => {
    invoke.mockResolvedValue({ mime_type: "image/png", bytes: [137, 80, 78, 71] });
    render(<PersonaAvatar personaId="persona-a" displayName="Jarvis" avatarExtension="png" />);

    fireEvent.error(await screen.findByAltText("Jarvis avatar"));

    expect((await screen.findByLabelText("Jarvis avatar")).textContent).toBe("J");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:persona-avatar");
  });

  it("reloads a replacement avatar with the same extension when its revision changes", async () => {
    let imageNumber = 0;
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => `blob:persona-avatar-${++imageNumber}`) });
    invoke.mockResolvedValue({ mime_type: "image/png", bytes: [137, 80, 78, 71] });
    const { rerender } = render(<PersonaAvatar personaId="persona-a" displayName="Jarvis" avatarExtension="png" revision={1} className="header-avatar" />);

    const image = await screen.findByAltText("Jarvis avatar");
    await waitFor(() => expect(image.getAttribute("src")).toBe("blob:persona-avatar-1"));
    expect(image.className).toContain("persona-avatar");
    expect(image.className).toContain("header-avatar");

    rerender(<PersonaAvatar personaId="persona-a" displayName="Jarvis" avatarExtension="png" revision={2} className="header-avatar" />);
    await waitFor(() => expect(screen.getByAltText("Jarvis avatar").getAttribute("src")).toBe("blob:persona-avatar-2"));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:persona-avatar-1");
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it("cleans up the previous Persona URL before rendering a switched Persona", async () => {
    let imageNumber = 0;
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => `blob:persona-avatar-${++imageNumber}`) });
    invoke.mockResolvedValue({ mime_type: "image/png", bytes: [137, 80, 78, 71] });
    const { rerender } = render(<PersonaAvatar personaId="persona-a" displayName="Jarvis" avatarExtension="png" />);
    await screen.findByAltText("Jarvis avatar");

    rerender(<PersonaAvatar personaId="persona-b" displayName="Nova" avatarExtension="png" />);

    await waitFor(() => expect(screen.getByAltText("Nova avatar").getAttribute("src")).toBe("blob:persona-avatar-2"));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:persona-avatar-1");
    expect(invoke).toHaveBeenLastCalledWith("read_persona_avatar", { personaId: "persona-b" });
  });
});

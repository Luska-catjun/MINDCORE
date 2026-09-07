import { render, screen, waitFor } from "@testing-library/react";
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
  });
});

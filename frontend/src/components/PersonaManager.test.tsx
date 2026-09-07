import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { PersonaManager, type PersonaSummary } from "./PersonaManager";

const personas: PersonaSummary[] = [
  { persona_id: "persona-a", display_name: "Jarvis", created_at: 1, last_used_at: 2, active: true, avatar_extension: "png" },
  { persona_id: "persona-b", display_name: "Nova", created_at: 3, last_used_at: null, active: false, avatar_extension: null },
];

describe("PersonaManager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    invoke.mockImplementation((command: string) => {
      if (command === "generic_identity_template") return Promise.resolve("generic identity");
      if (command === "create_persona") return Promise.resolve({ ...personas[1], active: false });
      return Promise.resolve();
    });
  });

  it("creates a Persona through name, identity, database, and review", async () => {
    const changed = vi.fn().mockResolvedValue(undefined);
    render(<PersonaManager mode="add" personas={personas} onClose={vi.fn()} onChanged={changed} />);
    await userEvent.type(screen.getByLabelText("Persona Name"), "Nova");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByLabelText("Persona Identity")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.type(screen.getByLabelText("Persona Database URL"), "libsql://nova.example");
    await userEvent.type(screen.getByLabelText("Persona Database Token"), "test-token");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Create Persona" }));

    expect(invoke).toHaveBeenCalledWith("create_persona", {
      draft: {
        display_name: "Nova",
        database_url: "libsql://nova.example",
        database_auth_token: "test-token",
      },
      identity: "generic identity",
    });
    expect(changed).toHaveBeenCalledWith("persona-b");
  });

  it("imports an optional avatar only after the Persona profile exists", async () => {
    const changed = vi.fn().mockResolvedValue(undefined);
    render(<PersonaManager mode="add" personas={personas} onClose={vi.fn()} onChanged={changed} />);
    await userEvent.type(screen.getByLabelText("Persona Name"), "Nova");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    const file = new File(["avatar"], "nova.png", { type: "image/png" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new Uint8Array([137, 80, 78, 71]).buffer });
    fireEvent.change(screen.getByLabelText("Persona Avatar"), { target: { files: [file] } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.type(screen.getByLabelText("Persona Database URL"), "libsql://nova.example");
    await userEvent.type(screen.getByLabelText("Persona Database Token"), "test-token");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Create Persona" }));

    expect(invoke).toHaveBeenCalledWith("set_persona_avatar", {
      personaId: "persona-b",
      bytes: [137, 80, 78, 71],
    });
  });

  it("requires exact confirmation and never offers deletion of the active Persona", async () => {
    render(<PersonaManager mode="manage" personas={personas} onClose={vi.fn()} onChanged={vi.fn().mockResolvedValue(undefined)} />);
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" }) as HTMLButtonElement[];
    expect(deleteButtons[0].disabled).toBe(true);
    expect(deleteButtons[1].disabled).toBe(false);
    await userEvent.click(deleteButtons[1]);
    const confirmation = screen.getByLabelText("Delete Nova");
    const confirmButton = screen.getByRole("button", { name: "Confirm Delete" }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);
    await userEvent.type(confirmation, "DELETE Nova");
    expect(confirmButton.disabled).toBe(false);
    await userEvent.click(confirmButton);
    expect(invoke).toHaveBeenCalledWith("delete_persona", {
      personaId: "persona-b",
      confirmation: "DELETE Nova",
    });
  });

  it("renders managed avatar controls and returns to fallback after removal", async () => {
    const changed = vi.fn().mockResolvedValue(undefined);
    render(<PersonaManager mode="manage" personas={personas} onClose={vi.fn()} onChanged={changed} />);
    expect(screen.getByLabelText("Jarvis avatar")).toBeTruthy();
    const removeButtons = screen.getAllByRole("button", { name: "Remove avatar" }) as HTMLButtonElement[];
    expect(removeButtons[0].disabled).toBe(false);
    expect(removeButtons[1].disabled).toBe(true);
    await userEvent.click(removeButtons[0]);
    expect(invoke).toHaveBeenCalledWith("remove_persona_avatar", { personaId: "persona-a" });
    expect(changed).toHaveBeenCalled();
  });
});

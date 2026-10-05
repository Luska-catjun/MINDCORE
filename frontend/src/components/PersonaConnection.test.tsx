import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PersonaConnection } from "./PersonaConnection";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("PersonaConnection", () => {
  it.each([
    ["BOUND_MATCH", "Persona connected"],
    ["UNBOUND", "Not connected yet"],
    ["BOUND_MISMATCH", "Persona mismatch"],
    ["DB_UNAVAILABLE", "Connection unavailable"],
  ])("shows binding state %s independently from database and provider", async (bindingState, label) => {
    invoke.mockResolvedValue({ llm_provider: "gemini", provider_key_configured: { gemini: true } });
    render(<PersonaConnection personaName="Mira" bindingState={bindingState} runtimeConnected
      databaseStatus="connected" onReconnect={vi.fn()} />);
    expect(await screen.findByText(label)).toBeTruthy();
    expect(screen.getByText("Connected", { selector: "h2" })).toBeTruthy();
    expect(screen.getByText("Credential configured")).toBeTruthy();
    expect(screen.queryByText(/DATABASE_AUTH_TOKEN|GEMINI_API_KEY/)).toBeNull();
  });

  it("performs an explicit reconnect check", async () => {
    const reconnect = vi.fn().mockResolvedValue(undefined);
    invoke.mockResolvedValue({ llm_provider: "gemini", provider_key_configured: {} });
    render(<PersonaConnection personaName="Mira" bindingState="UNBOUND" runtimeConnected
      databaseStatus="error" onReconnect={reconnect} />);
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    expect(reconnect).toHaveBeenCalledOnce();
    expect(screen.getByText("Unavailable", { selector: "h2" })).toBeTruthy();
  });
});

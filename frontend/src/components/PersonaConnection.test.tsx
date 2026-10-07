import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PersonaConnection } from "./PersonaConnection";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

describe("PersonaConnection", () => {
  it.each([
    ["BOUND_MATCH", "Persona 연결됨"],
    ["UNBOUND", "아직 연결되지 않음"],
    ["BOUND_MISMATCH", "Persona 불일치"],
    ["DB_UNAVAILABLE", "연결 확인 불가"],
  ])("shows binding state %s independently from database and provider", async (bindingState, label) => {
    invoke.mockResolvedValue({ llm_provider: "gemini", provider_key_configured: { gemini: true } });
    render(<PersonaConnection personaName="Mira" bindingState={bindingState} runtimeConnected
      databaseStatus="connected" onReconnect={vi.fn()} />);
    expect(await screen.findByText(label)).toBeTruthy();
    expect(screen.getByText("연결됨", { selector: "h2" })).toBeTruthy();
    expect(screen.getByText("인증 정보 설정됨")).toBeTruthy();
    expect(screen.queryByText(/DATABASE_AUTH_TOKEN|GEMINI_API_KEY/)).toBeNull();
  });

  it("performs an explicit reconnect check", async () => {
    const reconnect = vi.fn().mockResolvedValue(undefined);
    invoke.mockResolvedValue({ llm_provider: "gemini", provider_key_configured: {} });
    render(<PersonaConnection personaName="Mira" bindingState="UNBOUND" runtimeConnected
      databaseStatus="error" onReconnect={reconnect} />);
    await userEvent.click(screen.getByRole("button", { name: "다시 연결" }));
    expect(reconnect).toHaveBeenCalledOnce();
    expect(screen.getByText("연결 불가", { selector: "h2" })).toBeTruthy();
  });
});

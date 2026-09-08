import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { SetupWizard } from "./SetupWizard";

const databaseUrl = () => screen.getByPlaceholderText("Turso Database URL");
const databaseToken = () => screen.getByPlaceholderText("Turso Auth Token");
const continueButton = () => screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
const expectContinueDisabled = () => expect(continueButton().disabled).toBe(true);
const expectContinueEnabled = () => expect(continueButton().disabled).toBe(false);
const providerModels = { gemini: "gemini-3.5-flash-lite", groq: "qwen/qwen3.6-27b", anthropic: "claude-sonnet-5", xai: "grok-4.6", openai: "gpt-5.6-luna" };

describe("SetupWizard validation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    invoke.mockImplementation((name: string) =>
      name === "generic_identity_template" ? Promise.resolve("IDENTITY") : Promise.resolve("Connected"),
    );
  });

  async function enterDatabase() {
    render(<SetupWizard onComplete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Get Started" }));
  }

  async function validateDatabase() {
    fireEvent.change(databaseUrl(), { target: { value: "libsql://db" } });
    fireEvent.change(databaseToken(), { target: { value: "token-a" } });
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(expectContinueEnabled);
  }

  it("blocks untested and failed database Next, then invalidates on edit", async () => {
    await enterDatabase();
    expectContinueDisabled();
    invoke.mockRejectedValueOnce(new Error("no"));
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(expectContinueDisabled);
    await validateDatabase();
    fireEvent.change(databaseUrl(), { target: { value: "libsql://changed" } });
    expectContinueDisabled();
  });

  it("uses the fresh database draft before Persona and LLM setup are complete", async () => {
    await enterDatabase();
    fireEvent.change(databaseUrl(), { target: { value: "libsql://db" } });
    fireEvent.change(databaseToken(), { target: { value: "token-a" } });
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("run_setup_action", {
      action: "database",
      draft: expect.objectContaining({
        user_display_name: "",
        persona_display_name: "",
        llm_provider: "gemini",
        llm_model: "gemini-3.5-flash-lite",
        provider_models: expect.objectContaining({
          gemini: "gemini-3.5-flash-lite",
          anthropic: "claude-sonnet-5",
          openai: "gpt-5.6-luna",
        }),
      }),
    }));
  });

  it("preserves database values through LLM Back navigation", async () => {
    await enterDatabase();
    await validateDatabase();
    await userEvent.click(continueButton());
    expect(screen.getByRole("heading", { name: "Language Model" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect((databaseUrl() as HTMLInputElement).value).toBe("libsql://db");
    expect((databaseToken() as HTMLInputElement).value).toBe("token-a");
    expectContinueEnabled();
  });

  it("requires current LLM validation and invalidates provider/key changes", async () => {
    await enterDatabase();
    await validateDatabase();
    await userEvent.click(continueButton());
    expectContinueDisabled();

    invoke.mockRejectedValueOnce(new Error("no"));
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(expectContinueDisabled);
    fireEvent.change(screen.getByPlaceholderText("Gemini API Key"), { target: { value: "key-a" } });
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(expectContinueEnabled);
    fireEvent.change(screen.getByPlaceholderText("Gemini API Key"), { target: { value: "key-b" } });
    expectContinueDisabled();
    fireEvent.click(screen.getByLabelText("Groq"));
    expectContinueDisabled();
  });

  it("preserves LLM and persona state across every Back transition", async () => {
    await enterDatabase();
    await validateDatabase();
    await userEvent.click(continueButton());
    fireEvent.change(screen.getByPlaceholderText("Gemini API Key"), { target: { value: "key-a" } });
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(expectContinueEnabled);
    await userEvent.click(continueButton());
    await screen.findByRole("heading", { name: "Persona Setup" });
    fireEvent.change(screen.getByPlaceholderText("Persona name"), { target: { value: "Diana" } });
    fireEvent.change(screen.getByPlaceholderText("Your display name"), { target: { value: "Luska" } });
    await waitFor(expectContinueEnabled);
    await userEvent.click(continueButton());
    await screen.findByRole("heading", { name: "Review / Initialize" });

    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect((screen.getByPlaceholderText("Persona name") as HTMLInputElement).value).toBe("Diana");
    expect((screen.getByPlaceholderText("Your display name") as HTMLInputElement).value).toBe("Luska");
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect((screen.getByPlaceholderText("Gemini API Key") as HTMLInputElement).value).toBe("key-a");
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect((databaseUrl() as HTMLInputElement).value).toBe("libsql://db");
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("heading", { name: "Welcome to MindCore" })).toBeTruthy();
  });

  it("requires a valid persona name before Review", async () => {
    await enterDatabase();
    await validateDatabase();
    await userEvent.click(continueButton());
    fireEvent.change(screen.getByPlaceholderText("Gemini API Key"), { target: { value: "key-a" } });
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(expectContinueEnabled);
    await userEvent.click(continueButton());
    await screen.findByRole("heading", { name: "Persona Setup" });
    expectContinueDisabled();
    fireEvent.change(screen.getByPlaceholderText("Your display name"), { target: { value: "Luska" } });
    fireEvent.change(screen.getByPlaceholderText("Persona name"), { target: { value: "새 페르소나" } });
    await waitFor(expectContinueEnabled);
    fireEvent.change(screen.getByPlaceholderText("Persona name"), { target: { value: "bad\u0000name" } });
    expectContinueDisabled();
  });

  it("ignores a stale database validation result", async () => {
    let resolve!: () => void;
    invoke.mockImplementationOnce(() => new Promise<void>((done) => { resolve = done; }));
    await enterDatabase();
    fireEvent.change(databaseUrl(), { target: { value: "libsql://a" } });
    fireEvent.change(databaseToken(), { target: { value: "token-a" } });
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    fireEvent.change(databaseUrl(), { target: { value: "libsql://b" } });
    resolve();
    await waitFor(expectContinueDisabled);
    fireEvent.change(databaseUrl(), { target: { value: "libsql://a" } });
    expectContinueDisabled();
  });

  it("lets reconfigure retain configured secrets without returning plaintext", async () => {
    invoke.mockImplementation((name: string) => {
      if (name === "get_config_metadata") {
        return Promise.resolve({
          database_url: "libsql://existing",
          llm_provider: "gemini",
          user_display_name: "Luska",
          persona_display_name: "Diana",
          turso_token_configured: true,
          provider_models: { gemini: "gemini-custom", groq: "groq-custom", anthropic: "claude-custom", xai: "grok-custom", openai: "gpt-custom" },
          provider_key_configured: { gemini: true, groq: false, anthropic: false, xai: false, openai: false },
        });
      }
      return Promise.resolve("Connected");
    });
    render(<SetupWizard onComplete={vi.fn()} reconfigure />);
    await userEvent.click(screen.getByRole("button", { name: "Edit configuration" }));
    await waitFor(() => expect((databaseUrl() as HTMLInputElement).value).toBe("libsql://existing"));
    expect((screen.getByPlaceholderText("Configured — leave blank to keep") as HTMLInputElement).value).toBe("");
    expectContinueEnabled();
    await userEvent.click(continueButton());
    expect((screen.getByPlaceholderText("Configured — leave blank to keep") as HTMLInputElement).value).toBe("");
    expectContinueEnabled();
  });

  it("renders five providers with a separate editable model field", async () => {
    await enterDatabase();
    await validateDatabase();
    await userEvent.click(continueButton());
    for (const label of ["Gemini", "Groq", "Claude", "Grok", "OpenAI (GPT)"]) expect(screen.getByLabelText(label)).toBeTruthy();
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("gemini-3.5-flash-lite");
  });

  it("preserves provider-specific model and key drafts and preflights the exact selection", async () => {
    await enterDatabase();
    await validateDatabase();
    await userEvent.click(continueButton());
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gemini-custom" } });
    fireEvent.change(screen.getByPlaceholderText("Gemini API Key"), { target: { value: "gemini-key" } });
    fireEvent.click(screen.getByLabelText("OpenAI (GPT)"));
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-custom" } });
    fireEvent.change(screen.getByPlaceholderText("OpenAI (GPT) API Key"), { target: { value: "openai-key" } });
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("run_setup_action", {
      action: "llm",
      draft: expect.objectContaining({ llm_provider: "openai", llm_model: "gpt-custom", api_key: "openai-key" }),
    }));
    fireEvent.click(screen.getByLabelText("Gemini"));
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("gemini-custom");
    expect((screen.getByPlaceholderText("Gemini API Key") as HTMLInputElement).value).toBe("gemini-key");
  });

  it("loads the selected provider model during reconfigure without returning its key", async () => {
    invoke.mockImplementation((name: string) => name === "get_config_metadata" ? Promise.resolve({
      database_url: "libsql://existing", llm_provider: "anthropic", user_display_name: "Luska", persona_display_name: "Jarvis", turso_token_configured: true,
      provider_models: { gemini: "gemini-custom", groq: "groq-custom", anthropic: "claude-custom", xai: "grok-custom", openai: "gpt-custom" },
      provider_key_configured: { gemini: true, groq: true, anthropic: true, xai: false, openai: false },
    }) : Promise.resolve("Connected"));
    render(<SetupWizard onComplete={vi.fn()} reconfigure />);
    await userEvent.click(screen.getByRole("button", { name: "Edit configuration" }));
    await waitFor(() => expect((databaseUrl() as HTMLInputElement).value).toBe("libsql://existing"));
    await userEvent.click(continueButton());
    expect((screen.getByLabelText("Claude") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("claude-custom");
    expect((screen.getByPlaceholderText("Configured — leave blank to keep") as HTMLInputElement).value).toBe("");
  });

  it("loads and reviews separate global User and Persona names during reconfigure", async () => {
    invoke.mockImplementation((name: string) => name === "get_config_metadata" ? Promise.resolve({
      database_url: "libsql://existing", llm_provider: "gemini", user_display_name: "Luska", persona_display_name: "Jarvis", turso_token_configured: true,
      provider_models: providerModels,
      provider_key_configured: { gemini: true, groq: false, anthropic: false, xai: false, openai: false },
    }) : Promise.resolve("Connected"));
    render(<SetupWizard onComplete={vi.fn()} reconfigure />);
    await userEvent.click(screen.getByRole("button", { name: "Edit configuration" }));
    await waitFor(() => expect((databaseUrl() as HTMLInputElement).value).toBe("libsql://existing"));
    await userEvent.click(continueButton());
    await userEvent.click(continueButton());
    expect((screen.getByPlaceholderText("Your display name") as HTMLInputElement).value).toBe("Luska");
    expect((screen.getByPlaceholderText("Persona name") as HTMLInputElement).value).toBe("Jarvis");
    await userEvent.click(continueButton());
    expect(screen.getByText("User: Luska")).toBeTruthy();
    expect(screen.getByText("Persona: Jarvis")).toBeTruthy();
  });
});

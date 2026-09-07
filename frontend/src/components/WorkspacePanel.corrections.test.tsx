import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  observeMessages: vi.fn(), deleteMessage: vi.fn(),
  observeMemory: vi.fn(), correctMemory: vi.fn(), deleteMemory: vi.fn(),
  observeKnowledge: vi.fn(), correctKnowledge: vi.fn(), deleteKnowledge: vi.fn(),
  observePreferences: vi.fn(), correctPersonaPreference: vi.fn(), deletePersonaPreference: vi.fn(),
  observeNarratives: vi.fn(), correctNarrative: vi.fn(), deleteNarrative: vi.fn(),
  observeSelfModel: vi.fn(), correctSelfModel: vi.fn(), deleteSelfModel: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}));

import { WorkspacePanel } from "./WorkspacePanel";

const noop = () => undefined;
const memory = { id: "memory-1", content: "Original memory", importance: 0.7, memory_strength: 0.8, effective_strength: 0.8, recall_frequency: 1, created_at: "2026-01-01T00:00:00Z", source_episode_id: null };
const knowledge = { id: "knowledge-1", canonical_name: "Topic", knowledge_type: "fact", subject_key: "topic", status: "active", summary: "Original knowledge", confidence: 0.8, reinforcement_count: 1, source_type: "user", source_episode_id: null, fact_count: 0, learning_session_count: 0, facts: [], first_learned_at: null, last_reinforced_at: null };
const preference = { id: "preference-1", subject: "topic", display_name: "Original preference", status: "stable", affinity: 0.8, confidence: 0.8, evidence_count: 2, positive_evidence: 2, negative_evidence: 0, curiosity_evidence: 0, first_observed_at: null, last_observed_at: null, stabilized_at: null, recent_evidence: [] };
const narrative = { id: "narrative-1", category: "pattern", subject_key: "topic", status: "established", summary: "Original narrative", confidence: 0.8, evidence_count: 2, distinct_episode_count: 1, distinct_conversation_count: 1, activation_eligible: true, attention_score: null, attention_reasons: [], first_observed_at: null, last_observed_at: null, evidence: [] };
const selfModel = { id: "self-1", category: "belief", subject: "self", status: "established", summary: "Original self model", confidence: 0.8, support_count: 2, independent_source_count: 1, current: true, attention_score: null, source_types: [], first_observed_at: null, last_reinforced_at: null, evidence: [] };
const message = { id: "message-1", conversation_id: "conversation-1", role: "user", content: "Message to delete", created_at: "2026-01-01T00:00:00Z" };

describe("Observation corrections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.observeMessages.mockResolvedValue({ items: [message], total: 1, limit: 100, offset: 0, query_latency_ms: 1 });
    apiMock.observeMemory.mockResolvedValue({ items: [memory], total: 1, query_latency_ms: 1 });
    apiMock.observeKnowledge.mockResolvedValue({ items: [knowledge], total: 1, query_latency_ms: 1 });
    apiMock.observePreferences.mockResolvedValue({ diana_preferences: [preference], user_preferences: [], query_latency_ms: 1 });
    apiMock.observeNarratives.mockResolvedValue({ items: [narrative], total: 1, query_latency_ms: 1 });
    apiMock.observeSelfModel.mockResolvedValue({ items: [selfModel], total: 1, query_latency_ms: 1 });
    for (const method of [apiMock.correctMemory, apiMock.deleteMemory, apiMock.correctKnowledge, apiMock.deleteKnowledge, apiMock.correctPersonaPreference, apiMock.deletePersonaPreference, apiMock.correctNarrative, apiMock.deleteNarrative, apiMock.correctSelfModel, apiMock.deleteSelfModel]) method.mockResolvedValue({});
    apiMock.deleteMessage.mockResolvedValue({ id: message.id, deleted: true });
  });

  async function open(view: "memory" | "knowledge" | "preferences" | "narratives" | "self-model", text: string) {
    render(<WorkspacePanel view={view} backendStatus="connected" onToggleSidebar={noop} />);
    await screen.findByText(text);
  }

  it("saves a memory correction and refetches the durable section", async () => {
    await open("memory", "Original memory");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByRole("textbox");
    await userEvent.clear(input);
    await userEvent.type(input, "Corrected memory");
    await userEvent.click(screen.getByRole("button", { name: "Save Correction" }));
    await waitFor(() => expect(apiMock.correctMemory).toHaveBeenCalledWith("memory-1", "Corrected memory"));
    expect(apiMock.observeMemory).toHaveBeenCalledTimes(2);
  });

  it("keeps a memory visible when its correction fails", async () => {
    apiMock.correctMemory.mockRejectedValueOnce(new Error("network"));
    await open("memory", "Original memory");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save Correction" }));
    expect(await screen.findByText(/Could not update this item/)).toBeTruthy();
    expect(screen.getAllByText("Original memory").length).toBeGreaterThan(0);
  });

  it("provides bounded Knowledge and Persona Preference corrections", async () => {
    await open("knowledge", "Original knowledge");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save Correction" }));
    await waitFor(() => expect(apiMock.correctKnowledge).toHaveBeenCalledWith("knowledge-1", "Original knowledge"));

    render(<WorkspacePanel view="preferences" backendStatus="connected" onToggleSidebar={noop} />);
    await screen.findByText("Original preference");
    await userEvent.click(screen.getByText("Original preference"));
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" }).at(-1)!);
    await userEvent.click(screen.getByRole("button", { name: "Save Correction" }));
    await waitFor(() => expect(apiMock.correctPersonaPreference).toHaveBeenCalledWith("preference-1", "Original preference"));
  });

  it("warns before identity-critical Narrative and Self Model corrections", async () => {
    await open("narratives", "Original narrative");
    await userEvent.click(screen.getByText("pattern"));
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByText(/self-understanding or long-term behavior/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    render(<WorkspacePanel view="self-model" backendStatus="connected" onToggleSidebar={noop} />);
    await screen.findByText("Original self model");
    await userEvent.click(screen.getByText("belief"));
    await userEvent.click(screen.getAllByRole("button", { name: "Edit" }).at(-1)!);
    expect(screen.getAllByText(/self-understanding or long-term behavior/).length).toBeGreaterThan(0);
  });

  it("requires an in-app delete confirmation before deleting a Memory", async () => {
    await open("memory", "Original memory");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiMock.deleteMemory).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Delete Item" }));
    await waitFor(() => expect(apiMock.deleteMemory).toHaveBeenCalledWith("memory-1"));
    expect(apiMock.observeMemory).toHaveBeenCalledTimes(2);
  });

  it("requires an in-app confirmation and removes a successfully deleted Message", async () => {
    const deleted = vi.fn();
    render(<WorkspacePanel view="messages" backendStatus="connected" onToggleSidebar={noop} onMessageDeleted={deleted} />);
    await screen.findByText("Message to delete");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiMock.deleteMessage).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Confirm message deletion" })).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Delete Message" }));
    await waitFor(() => expect(apiMock.deleteMessage).toHaveBeenCalledWith("message-1"));
    expect(screen.queryByText("Message to delete")).toBeNull();
    expect(deleted).toHaveBeenCalledWith("conversation-1", "message-1");
  });

  it("does not call the Message delete API when confirmation is cancelled", async () => {
    render(<WorkspacePanel view="messages" backendStatus="connected" onToggleSidebar={noop} />);
    await screen.findByText("Message to delete");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(apiMock.deleteMessage).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Confirm message deletion" })).toBeNull();
    expect(screen.getByText("Message to delete")).toBeTruthy();
  });

  it("keeps a Message visible on failure and allows retry", async () => {
    apiMock.deleteMessage.mockRejectedValueOnce(new Error("network"));
    render(<WorkspacePanel view="messages" backendStatus="connected" onToggleSidebar={noop} />);
    await screen.findByText("Message to delete");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete Message" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Message could not be deleted");
    expect(screen.getByText("Message to delete")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(apiMock.deleteMessage).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Message to delete")).toBeNull();
  });
});

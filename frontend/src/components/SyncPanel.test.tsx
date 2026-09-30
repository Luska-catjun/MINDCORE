import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
const open = vi.hoisted(() => vi.fn());
const save = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open, save }));

import { SyncPanel } from "./SyncPanel";

const persona = "71000000-0000-4000-8000-000000000001";
const peer = "72000000-0000-4000-8000-000000000002";
const conflict = { conflict_id: "a".repeat(64), entity_kind: "relationship",
  conflict_type: "CONCURRENT_MUTATION", peer_device_id: peer,
  first_seen_ms: 1, local_hash: "b".repeat(64), remote_hash: "c".repeat(64) };

describe("SyncPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    invoke.mockImplementation((_command: string, args: { action: string }) => {
      if (_command === "manual_sync_scope") return Promise.resolve({
        supported: true, scope: "MANUAL_SECURE_LOCAL_FILE_DESKTOP_PERSONAS",
      });
      if (args.action === "peers") return Promise.resolve(JSON.stringify({ device_id: "device-a",
        fingerprint: "d".repeat(64), peers: [{ device_id: peer, fingerprint: "e".repeat(64),
          state: "TRUSTED", paired_at: 1 }] }));
      if (args.action === "conflicts") return Promise.resolve(JSON.stringify({ conflicts: [conflict] }));
      if (args.action === "conflict-preview") return Promise.resolve(JSON.stringify({ category: "Relationship",
        this_device: "Trust: 0.8", peer_device: "Trust: 0.9", repair_required: false }));
      if (args.action === "pair-inspect") return Promise.resolve(JSON.stringify({ device_id: peer,
        fingerprint: "e".repeat(64) }));
      if (args.action === "export") return Promise.resolve(JSON.stringify({ delta_entries: 1,
        delta_conflicts: 0 }));
      if (args.action === "apply") return Promise.resolve(JSON.stringify({ applied: 1,
        idempotent: 0, source_device_id: peer }));
      return Promise.resolve("{}");
    });
    open.mockResolvedValue("/tmp/synthetic.mindcoresync");
    save.mockResolvedValue("/tmp/synthetic.mindcoresync");
  });

  it("scopes file actions and conflict resolution to the selected Persona", async () => {
    render(<SyncPanel personaId={persona} personaName="Synthetic" />);
    expect(await screen.findByText("Synthetic")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Export sync update" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({
      action: "export", personaId: persona, peerDeviceId: peer,
      artifactPath: "/tmp/synthetic.mindcoresync",
    })));
    await userEvent.click(screen.getByRole("button", { name: "Import sync update" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({
      action: "apply", personaId: persona,
    })));
    await userEvent.click(screen.getByRole("button", { name: "Compare versions" }));
    expect(await screen.findByText("Trust: 0.8")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Use peer version" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({
      action: "resolve", personaId: persona, conflictId: conflict.conflict_id,
      choice: "USE_PEER_VERSION",
    })));
  });

  it("requires explicit fingerprint confirmation for pairing", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("e".repeat(64));
    open.mockResolvedValue("/tmp/synthetic.mindcorepair");
    render(<SyncPanel personaId={persona} personaName="Synthetic" />);
    await screen.findByText("Synthetic");
    await userEvent.click(screen.getByRole("button", { name: "Import pairing file" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({
      action: "pair-import", confirmedFingerprint: "e".repeat(64),
    })));
    vi.restoreAllMocks();
  });

  it("uses the native save dialog for a public pairing file", async () => {
    save.mockResolvedValue("/tmp/synthetic.mindcorepair");
    render(<SyncPanel personaId={persona} personaName="Synthetic" />);
    await screen.findByText("Synthetic");
    await userEvent.click(screen.getByRole("button", { name: "Export pairing file" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({
      action: "pair-export", artifactPath: "/tmp/synthetic.mindcorepair",
    })));
  });

  it("confirms revocation before invoking the backend", async () => {
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<SyncPanel personaId={persona} personaName="Synthetic" />);
    await screen.findByRole("button", { name: "Revoke" });
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(invoke).not.toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({ action: "revoke" }));
    confirmation.mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({
      action: "revoke", peerDeviceId: peer,
    })));
    vi.restoreAllMocks();
  });

  it("requires a second confirmation for a shared tombstone", async () => {
    const base = invoke.getMockImplementation()!;
    invoke.mockImplementation((command: string, args: { action: string }) => args.action === "conflicts"
      ? Promise.resolve(JSON.stringify({ conflicts: [{ ...conflict, conflict_type: "TOMBSTONE_CONFLICT" }] }))
      : base(command, args));
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<SyncPanel personaId={persona} personaName="Synthetic" />);
    await userEvent.click(await screen.findByRole("button", { name: "Compare versions" }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete on both devices" }));
    expect(invoke).not.toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({ action: "resolve" }));
    confirmation.mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "Delete on both devices" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("manual_sync_action", expect.objectContaining({
      action: "resolve", choice: "DELETE_ON_BOTH_DEVICES", personaId: persona,
    })));
    vi.restoreAllMocks();
  });

  it("shows only the selected Persona's saved status after switching", async () => {
    const next = "71000000-0000-4000-8000-000000000002";
    window.localStorage.setItem(`mindcore-manual-sync-status:${persona}`, "P1 only");
    window.localStorage.setItem(`mindcore-manual-sync-status:${next}`, "P2 only");
    const view = render(<SyncPanel personaId={persona} personaName="P1" />);
    expect(await screen.findByText("P1 only")).toBeTruthy();
    view.rerender(<SyncPanel personaId={next} personaName="P2" />);
    expect(await screen.findByText("P2 only")).toBeTruthy();
    expect(screen.queryByText("P1 only")).toBeNull();
  });

  it("disables every sync action for an unsupported remote-backed Persona", async () => {
    invoke.mockImplementation((command: string) => command === "manual_sync_scope"
      ? Promise.resolve({ supported: false, scope: "MANUAL_SECURE_LOCAL_FILE_DESKTOP_PERSONAS" })
      : Promise.reject("unexpected sync mutation"));
    render(<SyncPanel personaId={persona} personaName="Remote Persona" />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent",
      expect.stringContaining("only for a local file Persona"));
    expect(screen.queryByRole("button", { name: "Export pairing file" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Import sync update" })).toBeNull();
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith("manual_sync_scope", { personaId: persona });
  });
});

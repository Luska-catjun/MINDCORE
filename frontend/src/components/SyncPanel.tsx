import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";

type Peer = { device_id: string; fingerprint: string; state: string; paired_at: number };
type Conflict = {
  conflict_id: string; entity_kind: string; conflict_type: string; peer_device_id: string;
  first_seen_ms: number; local_hash: string | null; remote_hash: string | null;
};
type Preview = {
  category: string; this_device?: string; peer_device?: string;
  repair_required: boolean; local_fingerprint?: string; peer_fingerprint?: string;
};
type SyncArguments = {
  action: string; personaId?: string; peerDeviceId?: string; artifactPath?: string;
  confirmedFingerprint?: string; conflictId?: string; choice?: string;
};

async function syncAction<T>(args: SyncArguments): Promise<T> {
  return JSON.parse(await invoke<string>("manual_sync_action", args)) as T;
}

function safeError(error: unknown): string {
  const code = typeof error === "string" ? error : "SYNC_OPERATION_FAILED";
  const messages: Record<string, string> = {
    UNKNOWN_PEER: "Unknown device. Pair it first.",
    REVOKED_PEER: "This device was revoked.",
    WRONG_PERSONA: "This file belongs to a different Persona.",
    AUTHENTICATION_FAILED: "The sync file is invalid or was changed.",
    ENVELOPE_MALFORMED: "The selected file is not a valid sync file.",
    REPLAY_REJECTED: "Already imported. No changes were made.",
    NO_CHANGES: "There are no local changes to export.",
    UNSUPPORTED_PROTOCOL: "This sync file uses an unsupported version.",
    OS_KEYRING_UNAVAILABLE: "Secure sync is unavailable. Local Persona chat still works.",
    SYNC_CRYPTO_UNAVAILABLE: "Secure sync is unavailable. Local Persona chat still works.",
    PAIRING_FINGERPRINT_MISMATCH: "The fingerprint did not match. Device trust was not added.",
    CONCURRENT_MUTATION: "A conflict needs your choice in the Conflict Inbox.",
    CONCURRENT_DELETE_MUTATION: "A delete/update conflict needs your choice.",
    TOMBSTONE_CONFLICT: "A delete/update conflict needs your choice.",
    RESOLUTION_CONFLICT: "Both devices resolved the same conflict differently. Choose again.",
    IDENTITY_PAYLOAD_CONFLICT: "An integrity conflict needs repair; no data was overwritten.",
    SYNC_LOCAL_DATABASE_REQUIRED: "Manual sync needs a local schema 24 Persona database on this device.",
  };
  return messages[code] ?? "Could not complete secure sync. Check the file and device trust.";
}

function short(value: string | null | undefined): string {
  return (value ?? "").slice(0, 12);
}

export function SyncPanel({ personaId, personaName }: { personaId: string | null; personaName: string }) {
  const currentPersona = useRef(personaId);
  currentPersona.current = personaId;
  const [scopeSupported, setScopeSupported] = useState<boolean | null>(null);
  const [identity, setIdentity] = useState<{ device_id: string; fingerprint: string } | null>(null);
  const [peers, setPeers] = useState<Peer[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [peerId, setPeerId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [preview, setPreview] = useState<{ conflict: Conflict; value: Preview } | null>(null);

  const peerName = (deviceId: string) => window.localStorage.getItem(`mindcore-sync-name:${deviceId}`)
    || `Device ${short(deviceId)}`;
  const rename = (deviceId: string) => {
    const value = window.prompt("Name this trusted device", peerName(deviceId));
    if (value?.trim()) {
      window.localStorage.setItem(`mindcore-sync-name:${deviceId}`, value.trim().slice(0, 60));
      setPeers((current) => [...current]);
    }
  };

  const statusKey = `mindcore-manual-sync-status:${personaId ?? "none"}`;
  const refresh = async () => {
    if (!personaId) return;
    const owner = personaId;
    const [devices, inbox] = await Promise.all([
      syncAction<{ device_id: string; fingerprint: string; peers: Peer[] }>({ action: "peers" }),
      syncAction<{ conflicts: Conflict[] }>({ action: "conflicts", personaId }),
    ]);
    if (currentPersona.current !== owner) return;
    setIdentity({ device_id: devices.device_id, fingerprint: devices.fingerprint });
    setPeers(devices.peers);
    setConflicts(inbox.conflicts);
    setPeerId((current) => devices.peers.some((peer) => peer.device_id === current && peer.state === "TRUSTED")
      ? current : (devices.peers.find((peer) => peer.state === "TRUSTED")?.device_id ?? ""));
  };

  useEffect(() => {
    setScopeSupported(null);
    setIdentity(null); setPeers([]); setConflicts([]); setPreview(null); setError("");
    setStatus(window.localStorage.getItem(statusKey) ?? "No manual sync yet for this Persona.");
    if (!personaId) return;
    const owner = personaId;
    void invoke<{ supported: boolean; scope: string }>("manual_sync_scope", { personaId })
      .then((result) => {
        if (currentPersona.current !== owner) return;
        if (!result.supported) { setScopeSupported(false); return; }
        void refresh().then(() => {
          if (currentPersona.current === owner) setScopeSupported(true);
        }).catch((failure) => {
          if (currentPersona.current === owner) {
            setScopeSupported(true);
            setError(safeError(failure));
          }
        });
      })
      .catch((failure) => {
        if (currentPersona.current !== owner) return;
        setScopeSupported(false);
        setError(safeError(failure));
      });
  // The selected Persona is the entire operation scope.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personaId]);

  const run = async (work: () => Promise<string>) => {
    if (busy) return;
    const owner = personaId;
    setBusy(true); setError("");
    try {
      const message = await work();
      if (message && currentPersona.current === owner) {
        setStatus(message);
        window.localStorage.setItem(statusKey, message);
      }
    } catch (failure) {
      if (currentPersona.current === owner) setError(safeError(failure));
    } finally {
      if (currentPersona.current === owner) {
        try { await refresh(); } catch { /* Keep the operation result visible. */ }
      }
      setBusy(false);
    }
  };

  const exportPair = () => void run(async () => {
    const path = await save({ defaultPath: "mindcore-device.mindcorepair",
      filters: [{ name: "MindCore pairing", extensions: ["mindcorepair"] }] });
    if (!path) return "";
    await syncAction({ action: "pair-export", artifactPath: path });
    return "Pairing file exported. Compare fingerprints through a separate trusted channel.";
  });

  const importPair = () => void run(async () => {
    const path = await open({ multiple: false, directory: false,
      filters: [{ name: "MindCore pairing", extensions: ["mindcorepair"] }] });
    if (!path || typeof path !== "string") return "";
    const candidate = await syncAction<{ device_id: string; fingerprint: string }>({
      action: "pair-inspect", artifactPath: path,
    });
    const typed = window.prompt(`Verify this fingerprint on the other device through a separate channel, then enter the full fingerprint:\n\n${candidate.fingerprint}`);
    if (!typed) return "";
    await syncAction({ action: "pair-import", artifactPath: path,
      confirmedFingerprint: typed.trim() });
    rename(candidate.device_id);
    return `Trusted device ${short(candidate.device_id)} paired.`;
  });

  const exportUpdate = () => void run(async () => {
    if (!personaId || !peerId) throw "UNKNOWN_PEER";
    const path = await save({ defaultPath: `mindcore-${short(personaId)}.mindcoresync`,
      filters: [{ name: "MindCore secure sync", extensions: ["mindcoresync"] }] });
    if (!path) return "";
    const result = await syncAction<{ delta_entries: number; delta_conflicts: number }>({
      action: "export", personaId, peerDeviceId: peerId, artifactPath: path,
    });
    return `Manual export for ${short(peerId)}: ${result.delta_entries} records, ${result.delta_conflicts} conflicts.`;
  });

  const importUpdate = () => void run(async () => {
    if (!personaId) throw "WRONG_PERSONA";
    const path = await open({ multiple: false, directory: false,
      filters: [{ name: "MindCore secure sync", extensions: ["mindcoresync"] }] });
    if (!path || typeof path !== "string") return "";
    const result = await syncAction<{ applied: number; idempotent: number; source_device_id: string }>({
      action: "apply", personaId, artifactPath: path,
    });
    window.localStorage.setItem(`mindcore-manual-sync-peer:${personaId}:${result.source_device_id}`,
      `Last manual import: ${new Date().toLocaleString()}`);
    return `Manual import from ${short(result.source_device_id)}: ${result.applied} applied, ${result.idempotent} already applied.`;
  });

  const revoke = (peer: Peer) => {
    if (!window.confirm(`Revoke ${short(peer.device_id)}? Future sync files from this device will be rejected.`)) return;
    void run(async () => {
      await syncAction({ action: "revoke", peerDeviceId: peer.device_id });
      return `Device ${short(peer.device_id)} revoked.`;
    });
  };

  const showConflict = (conflict: Conflict) => void run(async () => {
    if (!personaId) throw "WRONG_PERSONA";
    const value = await syncAction<Preview>({ action: "conflict-preview", personaId,
      conflictId: conflict.conflict_id });
    if (currentPersona.current === personaId) setPreview({ conflict, value });
    return "";
  });

  const resolve = (choice: string) => {
    if (!personaId || !preview) return;
    if (choice === "DELETE_ON_BOTH_DEVICES" &&
        !window.confirm("Mark this item deleted on both devices? Independent cognition records remain intact.")) return;
    const conflict = preview.conflict;
    void run(async () => {
      await syncAction({ action: "resolve", personaId, conflictId: conflict.conflict_id, choice });
      setPreview(null);
      return "Conflict resolved here. Export an update and import it on the other device.";
    });
  };

  if (!personaId) return <section className="sync-panel" aria-label="Sync and Devices">
    <h1>Sync / Devices</h1><p>Select a Persona to use manual secure sync.</p>
  </section>;
  if (scopeSupported === false) return <section className="sync-panel" aria-label="Sync and Devices">
    <h1>Sync / Devices</h1><div className="sync-card">
      <h2>{personaName}</h2>
      <p role="alert">This version supports manual secure sync only for a local file Persona.
        Sync actions are unavailable for this Persona storage backend.</p>
      {error && <p>{error}</p>}
    </div>
  </section>;
  if (scopeSupported === null) return <section className="sync-panel" aria-label="Sync and Devices">
    <h1>Sync / Devices</h1><p>Checking this Persona’s sync storage support…</p>
  </section>;

  return <section className="sync-panel" aria-label="Sync and Devices">
    <h1>Sync / Devices</h1>
    <p>Secure manual file sync. Your devices do not sync automatically.</p>
    <div className="sync-card"><h2>Selected Persona</h2>
      <strong>{personaName}</strong><p>{personaId ? `ID ${short(personaId)}` : "Select a Persona first."}</p>
      <p>Files for a different Persona are rejected.</p></div>
    <div className="sync-card"><h2>This device</h2>
      <p>{identity ? `Desktop · ${short(identity.device_id)}` : "Loading device identity…"}</p>
      {identity && <p>Fingerprint: <code>{short(identity.fingerprint)}</code></p>}
    </div>
    <div className="sync-card"><h2>Manual sync status</h2><p role="status">{status}</p>
      <p>{conflicts.length} pending conflict{conflicts.length === 1 ? "" : "s"}</p>
      {error && <p role="alert" className="sync-error">{error}</p>}
      <button type="button" disabled={busy} onClick={() => void refresh().catch((failure) => setError(safeError(failure)))}>Refresh status</button>
    </div>
    <div className="sync-card"><h2>Pair a device</h2>
      <p>Compare the full fingerprint on the other device through a separate trusted channel.</p>
      <div className="sync-actions"><button type="button" disabled={busy} onClick={exportPair}>Export pairing file</button>
        <button type="button" disabled={busy} onClick={importPair}>Import pairing file</button></div>
      <h3>Trusted devices</h3>
      {peers.length === 0 && <p>No devices paired yet.</p>}
      {peers.map((peer) => <div className="sync-peer" key={peer.device_id}>
        <strong>{peerName(peer.device_id)}</strong><span>{peer.state} · {short(peer.fingerprint)}</span>
        <span>{window.localStorage.getItem(`mindcore-manual-sync-peer:${personaId}:${peer.device_id}`) ?? "No manual import recorded"}</span>
        <button type="button" disabled={busy} onClick={() => rename(peer.device_id)}>Name device</button>
        {peer.state === "TRUSTED" && <button type="button" disabled={busy} onClick={() => revoke(peer)}>Revoke</button>}
      </div>)}
    </div>
    <div className="sync-card"><h2>Secure file transfer</h2>
      <label>Recipient device <select aria-label="Recipient device" value={peerId} onChange={(event) => setPeerId(event.target.value)}>
        {peers.filter((peer) => peer.state === "TRUSTED").map((peer) =>
          <option key={peer.device_id} value={peer.device_id}>Device {short(peer.device_id)} · {short(peer.fingerprint)}</option>)}</select></label>
      <div className="sync-actions"><button type="button" disabled={busy || !peerId || !personaId} onClick={exportUpdate}>Export sync update</button>
        <button type="button" disabled={busy || !personaId} onClick={importUpdate}>Import sync update</button></div>
    </div>
    <div className="sync-card"><h2>Conflict Inbox</h2>
      {conflicts.length === 0 && <p>No pending conflicts for this Persona.</p>}
      {conflicts.map((conflict) => <div className="sync-conflict" key={conflict.conflict_id}>
        <strong>{conflict.entity_kind.replaceAll("_", " ")}</strong>
        <span>{conflict.conflict_type} · Device {short(conflict.peer_device_id)}</span>
        <span>Detected {new Date(conflict.first_seen_ms).toLocaleString()}</span>
        {conflict.conflict_type === "IDENTITY_PAYLOAD_CONFLICT"
          ? <p>Integrity repair required. This entity was not overwritten. Fingerprints: {short(conflict.local_hash)} / {short(conflict.remote_hash)}</p>
          : <button type="button" disabled={busy} onClick={() => showConflict(conflict)}>Compare versions</button>}
      </div>)}
      {preview && <div className="sync-preview"><h3>{preview.value.category}</h3>
        <p><strong>This device</strong><br />{preview.value.this_device}</p>
        <p><strong>Device {short(preview.conflict.peer_device_id)}</strong><br />{preview.value.peer_device}</p>
        <div className="sync-actions">{preview.conflict.conflict_type === "TOMBSTONE_CONFLICT" ? <>
          <button type="button" disabled={busy} onClick={() => resolve("KEEP_UPDATED_ITEM")}>Keep updated item</button>
          <button type="button" disabled={busy} onClick={() => resolve("DELETE_ON_BOTH_DEVICES")}>Delete on both devices</button>
        </> : <>
          <button type="button" disabled={busy} onClick={() => resolve("KEEP_THIS_DEVICE_VERSION")}>Keep this device version</button>
          <button type="button" disabled={busy} onClick={() => resolve("USE_PEER_VERSION")}>Use peer version</button>
        </>}</div>
      </div>}
    </div>
  </section>;
}

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { avatarFileBytes, PersonaAvatar } from "./PersonaAvatar";

export interface PersonaSummary {
  persona_id: string;
  display_name: string;
  created_at: number;
  last_used_at: number | null;
  active: boolean;
  avatar_extension: string | null;
}

type Mode = "add" | "manage";
interface ProactiveSettings {
  enabled: boolean;
  cooldown_seconds: number;
  quiet_hours_enabled: boolean;
  quiet_start: string;
  quiet_end: string;
}
const defaultProactiveSettings: ProactiveSettings = {
  enabled: false, cooldown_seconds: 1800, quiet_hours_enabled: true,
  quiet_start: "23:00", quiet_end: "07:00",
};

interface PersonaManagerProps {
  mode: Mode;
  personas: PersonaSummary[];
  avatarRevision?: number;
  onClose: () => void;
  onChanged: (switchTo?: string) => Promise<void>;
}

const validName = (value: string) => {
  const name = value.trim();
  return name.length > 0 && [...name].length <= 80 && ![...name].some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  });
};

function AddPersona({ onClose, onChanged }: Pick<PersonaManagerProps, "onClose" | "onChanged">) {
  const [step, setStep] = useState(0);
  const [displayName, setDisplayName] = useState("");
  const [identity, setIdentity] = useState("");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [databaseAuthToken, setDatabaseAuthToken] = useState("");
  const [avatarBytes, setAvatarBytes] = useState<number[] | null>(null);
  const [avatarLoading, setAvatarLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void invoke<string>("generic_identity_template").then(setIdentity).catch(() => {
      setError("Could not load the generic identity template.");
    });
  }, []);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const persona = await invoke<PersonaSummary>("create_persona", {
        draft: {
          display_name: displayName.trim(),
          database_url: databaseUrl.trim(),
          database_auth_token: databaseAuthToken,
        },
        identity,
      });
      if (avatarBytes) {
        try {
          await invoke("set_persona_avatar", { personaId: persona.persona_id, bytes: avatarBytes });
        } catch {
          await onChanged(persona.persona_id);
          setError("Persona was created, but the avatar could not be saved.");
          return;
        }
      }
      await onChanged(persona.persona_id);
      onClose();
    } catch {
      setError("Persona creation failed. Existing Personas were not changed.");
    } finally {
      setBusy(false);
    }
  };

  const canContinue = step === 0
    ? validName(displayName)
    : step === 1
      ? identity.trim().length > 0 && !avatarLoading
      : databaseUrl.trim().length > 0 && databaseAuthToken.trim().length > 0;

  return <>
    <h2>Add Persona</h2>
    {step === 0 && <><label>Persona Name<input aria-label="Persona Name" maxLength={80} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label></>}
    {step === 1 && <><label>Identity<textarea aria-label="Persona Identity" value={identity} onChange={(event) => setIdentity(event.target.value)} /></label><label>Avatar image (optional)<input aria-label="Persona Avatar" type="file" accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; setAvatarLoading(true); void avatarFileBytes(file).then(setAvatarBytes).catch((reason: Error) => setError(reason.message)).finally(() => setAvatarLoading(false)); }} /></label><p className="workspace-muted">PNG, JPEG, or WebP under 2 MB. MindCore copies the image into this Persona profile.</p><p className="workspace-muted">Starts from the packaged generic identity. This identity belongs only to this Persona.</p></>}
    {step === 2 && <><label>Database URL<input aria-label="Persona Database URL" value={databaseUrl} onChange={(event) => setDatabaseUrl(event.target.value)} /></label><label>Database token<input aria-label="Persona Database Token" type="password" value={databaseAuthToken} onChange={(event) => setDatabaseAuthToken(event.target.value)} /></label></>}
    {step === 3 && <div className="persona-review"><p><b>Name:</b> {displayName.trim()}</p><p><b>Identity:</b> separate managed file</p><p><b>Database:</b> separate durable database</p><p>Provider settings are shared by the MindCore app.</p></div>}
    {error && <p className="error-banner">{error}</p>}
    <div className="setup-navigation">
      <button type="button" onClick={step === 0 ? onClose : () => setStep((current) => current - 1)}>{step === 0 ? "Cancel" : "Back"}</button>
      {step < 3
        ? <button type="button" disabled={!canContinue || busy} onClick={() => setStep((current) => current + 1)}>Continue</button>
        : <button type="button" disabled={busy} onClick={() => void create()}>{busy ? "Creating…" : "Create Persona"}</button>}
    </div>
  </>;
}

function ManagePersonas({ personas, avatarRevision = 0, onClose, onChanged }: PersonaManagerProps) {
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const activePersona = personas.find((persona) => persona.active);
  const [proactive, setProactive] = useState<ProactiveSettings>(defaultProactiveSettings);
  const [proactiveLoading, setProactiveLoading] = useState(false);
  const [proactiveSaving, setProactiveSaving] = useState(false);

  useEffect(() => {
    if (!activePersona) return;
    setProactiveLoading(true);
    void invoke<ProactiveSettings>("get_proactive_settings", { personaId: activePersona.persona_id })
      .then((loaded) => setProactive(loaded ?? defaultProactiveSettings))
      .catch(() => setError("Could not load proactive settings for the active Persona."))
      .finally(() => setProactiveLoading(false));
  }, [activePersona?.persona_id]);

  const saveProactive = async () => {
    if (!activePersona) return;
    setProactiveSaving(true);
    setError(null);
    try {
      const saved = await invoke<ProactiveSettings>("update_proactive_settings", {
        personaId: activePersona.persona_id,
        settings: proactive,
      });
      setProactive(saved);
      await onChanged(activePersona.persona_id);
    } catch {
      setError("Could not save proactive settings. Check the cooldown and quiet-hour values.");
    } finally {
      setProactiveSaving(false);
    }
  };

  const setAvatar = async (persona: PersonaSummary, file: File) => {
    setError(null);
    try {
      await invoke("set_persona_avatar", { personaId: persona.persona_id, bytes: await avatarFileBytes(file) });
      await onChanged();
    } catch {
      setError("Could not update the Persona avatar. Choose a PNG, JPEG, or WebP image under 2 MB.");
    }
  };

  const removeAvatar = async (persona: PersonaSummary) => {
    setError(null);
    try {
      await invoke("remove_persona_avatar", { personaId: persona.persona_id });
      await onChanged();
    } catch {
      setError("Could not remove the Persona avatar.");
    }
  };

  const rename = async (persona: PersonaSummary) => {
    setError(null);
    try {
      await invoke("update_persona", {
        personaId: persona.persona_id,
        draft: { display_name: renameValue.trim() },
      });
      setRenameId(null);
      await onChanged(persona.active ? persona.persona_id : undefined);
    } catch {
      setError("Could not rename the Persona. Names must be unique.");
    }
  };

  const remove = async (persona: PersonaSummary) => {
    setError(null);
    try {
      await invoke("delete_persona", {
        personaId: persona.persona_id,
        confirmation,
      });
      setDeleteId(null);
      setConfirmation("");
      await onChanged();
    } catch {
      setError("Could not delete the Persona. Switch away first and enter the exact confirmation.");
    }
  };

  return <>
    <h2>Manage Personas</h2>
    <div className="persona-list">
      {personas.map((persona) => <div className="persona-row" key={persona.persona_id}>
        <div className="persona-row-identity"><PersonaAvatar personaId={persona.persona_id} displayName={persona.display_name} avatarExtension={persona.avatar_extension} revision={avatarRevision} /><span><b>{persona.display_name}</b>{persona.active && <span> Active</span>}<small>{persona.persona_id}</small></span></div>
        <div>
          <button type="button" onClick={() => { setRenameId(persona.persona_id); setRenameValue(persona.display_name); }}>Rename</button>
          <label className="persona-avatar-change">Change avatar<input aria-label={`Avatar ${persona.display_name}`} type="file" accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" onChange={(event) => { const file = event.target.files?.[0]; if (file) void setAvatar(persona, file); event.currentTarget.value = ""; }} /></label>
          <button type="button" disabled={!persona.avatar_extension} onClick={() => void removeAvatar(persona)}>Remove avatar</button>
          <button type="button" disabled={persona.active || personas.length === 1} onClick={() => { setDeleteId(persona.persona_id); setConfirmation(""); }}>Delete</button>
        </div>
        {renameId === persona.persona_id && <div className="persona-action"><input aria-label={`Rename ${persona.display_name}`} value={renameValue} onChange={(event) => setRenameValue(event.target.value)} /><button type="button" disabled={!validName(renameValue)} onClick={() => void rename(persona)}>Save</button></div>}
        {deleteId === persona.persona_id && <div className="persona-action"><p>Type <code>DELETE {persona.display_name}</code>. The external database is never deleted.</p><input aria-label={`Delete ${persona.display_name}`} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /><button type="button" disabled={confirmation !== `DELETE ${persona.display_name}`} onClick={() => void remove(persona)}>Confirm Delete</button></div>}
      </div>)}
    </div>
    {activePersona && <section className="proactive-settings" aria-label="Proactive messages settings">
      <h3>Proactive messages · {activePersona.display_name}</h3>
      <p>When enabled, MindCore may start a conversation when its goals or needs make it appropriate. This works only while MindCore is running.</p>
      {proactiveLoading ? <p className="workspace-muted">Loading settings…</p> : <>
        <label><input type="checkbox" checked={proactive.enabled} onChange={(event) => setProactive((value) => ({ ...value, enabled: event.target.checked }))} />Allow this Persona to start conversations</label>
        <label>Minimum time between messages<select aria-label="Proactive cooldown" value={proactive.cooldown_seconds} onChange={(event) => setProactive((value) => ({ ...value, cooldown_seconds: Number(event.target.value) }))}>{[[300,"5 minutes"],[900,"15 minutes"],[1800,"30 minutes"],[3600,"1 hour"],[14400,"4 hours"],[86400,"1 day"]].map(([seconds,label]) => <option key={seconds} value={seconds}>{label}</option>)}</select></label>
        <label><input type="checkbox" checked={proactive.quiet_hours_enabled} onChange={(event) => setProactive((value) => ({ ...value, quiet_hours_enabled: event.target.checked }))} />Pause during quiet hours</label>
        <div className="proactive-hours"><label>Quiet hours start<input aria-label="Quiet hours start" type="time" value={proactive.quiet_start} onChange={(event) => setProactive((value) => ({ ...value, quiet_start: event.target.value }))} /></label><label>Quiet hours end<input aria-label="Quiet hours end" type="time" value={proactive.quiet_end} onChange={(event) => setProactive((value) => ({ ...value, quiet_end: event.target.value }))} /></label></div>
        <p className="workspace-muted">Quiet hours use your configured MindCore timezone. No messages are generated while the app is closed.</p>
        <button type="button" disabled={proactiveSaving || proactiveLoading} onClick={() => void saveProactive()}>{proactiveSaving ? "Saving…" : "Save proactive settings"}</button>
      </>}
    </section>}
    {error && <p className="error-banner">{error}</p>}
    <div className="setup-navigation"><button type="button" onClick={onClose}>Close</button></div>
  </>;
}

export function PersonaManager(props: PersonaManagerProps) {
  return <div className="persona-modal-backdrop" role="presentation">
    <section className="persona-modal" role="dialog" aria-modal="true" aria-label={props.mode === "add" ? "Add Persona" : "Manage Personas"}>
      {props.mode === "add" ? <AddPersona onClose={props.onClose} onChanged={props.onChanged} /> : <ManagePersonas {...props} />}
    </section>
  </div>;
}

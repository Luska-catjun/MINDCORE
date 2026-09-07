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

interface PersonaManagerProps {
  mode: Mode;
  personas: PersonaSummary[];
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

function ManagePersonas({ personas, onClose, onChanged }: PersonaManagerProps) {
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);

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
        <div className="persona-row-identity"><PersonaAvatar personaId={persona.persona_id} displayName={persona.display_name} avatarExtension={persona.avatar_extension} /><span><b>{persona.display_name}</b>{persona.active && <span> Active</span>}<small>{persona.persona_id}</small></span></div>
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

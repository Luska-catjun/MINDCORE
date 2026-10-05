import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

interface Props {
  personaName: string;
  bindingState: string;
  runtimeConnected: boolean;
  databaseStatus: "checking" | "connected" | "error";
  onReconnect: () => Promise<void>;
}

type ProviderMetadata = { llm_provider: string; provider_key_configured: Record<string, boolean> };

const bindingCopy: Record<string, { title: string; detail: string; tone: string }> = {
  BOUND_MATCH: { title: "Persona connected", detail: "This device is connected to the selected Persona.", tone: "success" },
  UNBOUND: { title: "Not connected yet", detail: "The Persona will bind when its first protected action runs.", tone: "warning" },
  BOUND_MISMATCH: { title: "Persona mismatch", detail: "This MindCore is bound to a different Persona. Protected actions are paused.", tone: "error" },
  DB_UNAVAILABLE: { title: "Connection unavailable", detail: "MindCore cannot verify the Persona connection right now.", tone: "error" },
};

export function PersonaConnection({ personaName, bindingState, runtimeConnected, databaseStatus, onReconnect }: Props) {
  const [provider, setProvider] = useState<ProviderMetadata | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  useEffect(() => {
    if (!runtimeConnected) return;
    void invoke<ProviderMetadata>("get_config_metadata").then(setProvider).catch(() => setProvider(null));
  }, [runtimeConnected]);

  const binding = bindingCopy[bindingState] ?? bindingCopy.DB_UNAVAILABLE;
  const providerName = provider?.llm_provider ?? "Provider";
  const providerConfigured = provider?.provider_key_configured?.[providerName] === true;
  return <section className="connection-page" aria-labelledby="connection-title">
    <div className="connection-heading"><span className="connection-orbit" aria-hidden="true"><i /><b /></span><div><p className="workspace-kicker">MINDCORE / CONNECTION</p><h1 id="connection-title">Persona Connection</h1><p>Connection, Persona identity, and provider setup are shown separately.</p></div></div>
    <div className="connection-grid">
      <article className="connection-card"><span className="connection-label">Persona</span><h2>{personaName}</h2><p className={`connection-state connection-${binding.tone}`}>{binding.title}</p><p>{binding.detail}</p></article>
      <article className="connection-card"><span className="connection-label">Persona database</span><h2 className={`connection-state connection-${databaseStatus === "connected" ? "success" : databaseStatus === "error" ? "error" : "pending"}`}>{databaseStatus === "connected" ? "Connected" : databaseStatus === "error" ? "Unavailable" : "Connecting"}</h2><p>{databaseStatus === "connected" ? "The database health check completed successfully." : "Database connectivity is independent from Persona binding."}</p></article>
      <article className="connection-card"><span className="connection-label">Provider setup</span><h2>{providerName}</h2><p className={`connection-state connection-${providerConfigured ? "success" : "warning"}`}>{providerConfigured ? "Credential configured" : "Credential not configured"}</p><p>Credential presence only; provider connectivity is checked when a request is made.</p></article>
    </div>
    <button className="connection-retry" type="button" disabled={reconnecting} onClick={() => { setReconnecting(true); void onReconnect().finally(() => setReconnecting(false)); }}>{reconnecting ? "Checking…" : "Reconnect"}</button>
  </section>;
}

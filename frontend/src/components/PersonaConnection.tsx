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
  BOUND_MATCH: { title: "Persona 연결됨", detail: "이 기기는 선택한 Persona에 연결되어 있습니다.", tone: "success" },
  UNBOUND: { title: "아직 연결되지 않음", detail: "첫 번째 보호된 작업을 실행하면 Persona가 연결됩니다.", tone: "warning" },
  BOUND_MISMATCH: { title: "Persona 불일치", detail: "다른 Persona에 연결되어 있어 보호된 작업을 멈췄습니다.", tone: "error" },
  DB_UNAVAILABLE: { title: "연결 확인 불가", detail: "현재 Persona 연결을 확인할 수 없습니다.", tone: "error" },
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
    <div className="connection-heading"><span className="connection-orbit" aria-hidden="true"><i /><b /></span><div><p className="workspace-kicker">MINDCORE / 연결</p><h1 id="connection-title">Persona 연결</h1><p>저장소 연결, Persona 연결, AI 설정의 상태를 확인합니다.</p></div></div>
    <div className="connection-grid">
      <article className="connection-card"><span className="connection-label">Persona</span><h2>{personaName}</h2><p className={`connection-state connection-${binding.tone}`}>{binding.title}</p><p>{binding.detail}</p></article>
      <article className="connection-card"><span className="connection-label">Persona 데이터베이스</span><h2 className={`connection-state connection-${databaseStatus === "connected" ? "success" : databaseStatus === "error" ? "error" : "pending"}`}>{databaseStatus === "connected" ? "연결됨" : databaseStatus === "error" ? "연결 불가" : "연결 중"}</h2><p>{databaseStatus === "connected" ? "데이터베이스 연결 확인을 완료했습니다." : "데이터베이스 연결과 Persona 연결은 각각 확인합니다."}</p></article>
      <article className="connection-card"><span className="connection-label">AI 제공자 설정</span><h2>{providerName}</h2><p className={`connection-state connection-${providerConfigured ? "success" : "warning"}`}>{providerConfigured ? "인증 정보 설정됨" : "인증 정보 미설정"}</p><p>인증 정보 설정 여부입니다. 제공자 연결은 요청할 때 확인합니다.</p></article>
    </div>
    <button className="connection-retry" type="button" disabled={reconnecting} onClick={() => { setReconnecting(true); void onReconnect().finally(() => setReconnecting(false)); }}>{reconnecting ? "확인 중…" : "다시 연결"}</button>
  </section>;
}

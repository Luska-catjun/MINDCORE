import { useState, type ReactNode } from "react";
import { CURRENT_VERSION } from "../releaseNotes";
export function AppSettings({ personaControls, connection, onConfigure, onOpenConfiguration, onUpdate }: { personaControls: ReactNode; connection: ReactNode; onConfigure: () => void; onOpenConfiguration: () => void; onUpdate: (action: "check" | "notes") => void }) {
  const [tab, setTab] = useState("Persona");
  return <section className="app-settings"><h1>앱 설정</h1><nav className="compact-subnav" aria-label="설정 분류">{["Persona", "연결", "AI 제공자", "업데이트", "일반"].map(value => <button key={value} aria-current={tab === value ? "page" : undefined} onClick={() => setTab(value)}>{value}</button>)}</nav>
    {tab === "Persona" && <div className="settings-card"><h2>Persona 선택과 관리</h2>{personaControls}</div>}
    {tab === "연결" && connection}
    {tab === "AI 제공자" && <div className="settings-card"><h2>Provider / Model / Credential</h2><p>선택한 Persona의 기존 설정 화면에서 제공자, 모델과 credential을 관리합니다.</p><button onClick={onConfigure}>Reconfigure Active Persona</button><button onClick={onOpenConfiguration}>Open Configuration</button></div>}
    {tab === "업데이트" && <div className="settings-card"><h2>MindCore 업데이트</h2><p>현재 버전 {CURRENT_VERSION}</p><button onClick={() => onUpdate("check")}>업데이트 확인</button><button onClick={() => onUpdate("notes")}>업데이트 내용</button><p>진행 상태는 앱 하단에 표시됩니다.</p></div>}
    {tab === "일반" && <div className="settings-card"><h2>앱 환경</h2><button onClick={onOpenConfiguration}>Open Configuration</button></div>}
  </section>;
}

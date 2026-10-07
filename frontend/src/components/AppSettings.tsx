import { useState, type ReactNode } from "react";
export function AppSettings({ personaControls, connection, proactive, onConfigure, onOpenConfiguration, selectedTab, onTabChange, onUpdateSurface }: { personaControls: ReactNode; connection: ReactNode; proactive: ReactNode; onUpdateSurface?: (node: HTMLDivElement | null) => void; selectedTab?: string; onTabChange?: (tab: string) => void; onConfigure: () => void; onOpenConfiguration: () => void }) {
  const [localTab, setLocalTab] = useState("Persona");
  const tab = selectedTab ?? localTab;
  const setTab = onTabChange ?? setLocalTab;
  return <section className="app-settings"><h1>앱 설정</h1><nav className="underline-nav settings-nav" aria-label="설정 분류">{["Persona", "연결", "AI 설정", "먼저 말 걸기", "업데이트", "일반"].map(value => <button key={value} aria-current={tab === value ? "page" : undefined} onClick={() => setTab(value)}>{value}</button>)}</nav>
    <div hidden={tab !== "Persona"} className="settings-card"><h2>Persona 선택과 관리</h2>{personaControls}</div>
    {tab === "연결" && connection}
    <div hidden={tab !== "AI 설정"} className="settings-card"><h2>AI 제공자와 모델</h2><p>현재 Persona의 설정 화면에서 제공자, 모델과 인증 정보를 관리합니다.</p><button onClick={onConfigure}>현재 Persona 재설정</button><button onClick={onOpenConfiguration}>설정 폴더 열기</button></div>
    <div hidden={tab !== "먼저 말 걸기"} className="settings-card">{proactive}</div>
    <div hidden={tab !== "업데이트"} className="settings-card"><h2>MindCore 업데이트</h2><div id="settings-update-surface" ref={onUpdateSurface} /></div>
    <div hidden={tab !== "일반"} className="settings-card"><h2>앱 환경</h2><button onClick={onOpenConfiguration}>설정 폴더 열기</button></div>
  </section>;
}

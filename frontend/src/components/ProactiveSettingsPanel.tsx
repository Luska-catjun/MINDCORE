import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
export interface ProactiveSettings {
  enabled: boolean; cooldown_seconds: number; quiet_hours_enabled: boolean; quiet_start: string; quiet_end: string;
}
// The native command owns validation, per-Persona persistence and sidecar restart.
export function ProactiveSettingsPanel({ personaId, onSaved }: { personaId?: string; onSaved: () => Promise<void> }) {
  const generation = useRef(0);
  const [settings, setSettings] = useState<ProactiveSettings | null>(null);
  const [saved, setSaved] = useState<ProactiveSettings | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
  useEffect(() => {
    const expected = ++generation.current;
    let current = true;
    setSettings(null); setSaved(null); setError(""); setMessage("");
    if (personaId) void invoke<ProactiveSettings>("get_proactive_settings", { personaId }).then(value => {
      if (current && value) { setSettings(value); setSaved(value); }
    }).catch(() => { if (current) setError("먼저 말 걸기 설정을 불러오지 못했습니다."); });
    return () => { current = false; if (generation.current === expected) generation.current++; };
  }, [personaId]);

  const save = async () => {
    if (!settings || !personaId) return;
    const expected = generation.current;
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await invoke<ProactiveSettings>("update_proactive_settings", { personaId, settings });
      if (generation.current !== expected) return;
      setSettings(value); setSaved(value);
      await onSaved(); setMessage("설정을 저장했습니다.");
    } catch { setError("설정을 저장하지 못했습니다. 시간과 메시지 간격을 확인해 주세요."); }
    finally { setBusy(false); }
  };
  const openNotifications = async () => {
    try { await invoke("open_notification_settings"); }
    catch { setError("시스템 알림 설정을 열지 못했습니다. 운영체제 설정에서 MindCore 알림을 관리해 주세요."); }
  };
  return <section className="proactive-settings" aria-label="먼저 말 걸기 설정"><h2>먼저 말 걸기</h2>
    <p>MindCore가 실행 중일 때, 적절한 시점에 현재 Persona가 대화를 시작할 수 있습니다.</p>
    <p role="status">{!personaId ? "Persona를 선택해 주세요." : !saved ? "설정 확인 중" : saved.enabled ? "활성 · 조건을 확인하며 대기 중" : "비활성"}</p>
    {settings && <><fieldset disabled={busy}><label><input type="checkbox" checked={settings.enabled} onChange={event => setSettings({ ...settings, enabled: event.target.checked })} />먼저 말 걸기 사용</label>
      <label>최소 메시지 간격 (초)<input type="number" min={300} max={604800} step={1} value={settings.cooldown_seconds} onChange={event => setSettings({ ...settings, cooldown_seconds: Number(event.target.value) })} /></label>
      <label><input type="checkbox" checked={settings.quiet_hours_enabled} onChange={event => setSettings({ ...settings, quiet_hours_enabled: event.target.checked })} />조용한 시간 사용</label>
      <div className="proactive-hours"><label>조용한 시간 시작<input type="time" value={settings.quiet_start} onChange={event => setSettings({ ...settings, quiet_start: event.target.value })} /></label><label>조용한 시간 종료<input type="time" value={settings.quiet_end} onChange={event => setSettings({ ...settings, quiet_end: event.target.value })} /></label></div>
      <p>조용한 시간은 Persona에 설정된 시간대를 따릅니다. 반복해서 답하지 않으면 먼저 말 걸기를 잠시 멈춥니다.</p>
      <button onClick={() => void save()}>{busy ? "저장 중…" : "설정 저장"}</button></fieldset></>}
    <h3>알림</h3><p>알림 허용과 해제는 운영체제 시스템 설정에서 관리합니다.</p>
    <button onClick={() => void openNotifications()}>시스템 알림 설정 열기</button><p>앱 내부 새 메시지 알림은 계속 표시됩니다. 먼저 말 걸기 설정과 알림 허용은 서로 다른 설정입니다.</p>
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}

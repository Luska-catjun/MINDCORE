import { useCallback, useEffect, useRef, useState } from "react";
import { loadDesktopUpdateService, updateErrorMessage, type UpdateInfo, type UpdateService } from "../services/updateService";
import { CURRENT_VERSION, CURRENT_RELEASE_NOTES } from "../releaseNotes";
import { ReleaseNotesModal } from "./ReleaseNotesModal";
import { safePublicUrl, openPublicUrl } from "../services/productSupport";
type UpdateState = "idle" | "checking" | "up-to-date" | "available" | "downloading" | "ready" | "installing" | "error";
// The support shell stays mounted across setup and workspace screens. Dismissal
// is session-scoped; a manual check intentionally opens the offer again.
const dismissedVersions = new Set<string>();
export function DesktopUpdater({ service: override, enabled = true, autoCheck = true, onStateChange }: { service?: UpdateService; enabled?: boolean; autoCheck?: boolean; onStateChange?: (state: string) => void }) {
  const [service, setService] = useState<UpdateService | null>(override ?? null), [version, setVersion] = useState(CURRENT_VERSION);
  const [state, setState] = useState<UpdateState>("idle"), [update, setUpdate] = useState<UpdateInfo | null>(null), [error, setError] = useState<string | null>(null);
  const [offer, setOffer] = useState(false), [notes, setNotes] = useState<{ version: string; markdown: string } | null>(null);
  const [progress, setProgress] = useState<{ downloadedBytes: number; contentLength?: number }>({ downloadedBytes: 0 });
  const checked = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onStateChange?.(state); }, [state, onStateChange]);
  useEffect(() => { if (override || !enabled) return; void loadDesktopUpdateService().then(value => { if (mounted.current) setService(value); }).catch(() => { if (mounted.current) setError("업데이트를 확인할 수 없습니다."); }); }, [override, enabled]);
  useEffect(() => { if (service) void service.getCurrentVersion().then(value => { if (mounted.current) setVersion(value); }).catch(() => undefined); }, [service]);
  const check = useCallback(async (manual: boolean) => {
    if (!service) return;
    setState("checking"); setError(null);
    try {
      const next = await service.check(); if (!mounted.current) return;
      if (next && next.version !== next.currentVersion) { setUpdate(next); setState("available"); setOffer(manual || !dismissedVersions.has(next.version)); }
      else { setUpdate(null); setOffer(false); setState("up-to-date"); }
    } catch (reason) { if (mounted.current) { setError(updateErrorMessage(reason, "check")); setState("error"); } }
  }, [service]);
  useEffect(() => { if (!service || !autoCheck || checked.current) return; checked.current = true; const timer = window.setTimeout(() => void check(false), 1500); return () => { window.clearTimeout(timer); checked.current = false; }; }, [service, autoCheck, check]);
  const later = () => { if (update) dismissedVersions.add(update.version); setOffer(false); };
  const download = async () => {
    if (!update) return; setOffer(false); setState("downloading"); setError(null); setProgress({ downloadedBytes: 0 });
    try { await update.download(setProgress); setState("ready"); setOffer(true); } catch (reason) { setError(updateErrorMessage(reason, "download")); setState("error"); }
  };
  const install = async () => { if (!service || !update) return; setState("installing"); setError(null); try { await service.installAndRelaunch(update); } catch (reason) { setError(updateErrorMessage(reason, "install")); setState("error"); } };
  const busy = ["checking", "downloading", "installing"].includes(state);
  const percentage = progress.contentLength ? Math.min(100, Math.round(progress.downloadedBytes / progress.contentLength * 100)) + "%" : "";
  return <section className="desktop-updater" aria-label="MindCore updates"><span>MindCore · 버전 {version}</span><button onClick={() => setNotes({ version, markdown: CURRENT_RELEASE_NOTES })}>업데이트 내용</button><button disabled={!service || busy} onClick={() => void check(true)}>업데이트 확인</button>
    {state === "up-to-date" && <span>최신 버전을 사용 중입니다.</span>}{state === "checking" && <span>업데이트 확인 중…</span>}{state === "downloading" && <span>업데이트 다운로드 중… {percentage}</span>}{state === "installing" && <span>업데이트 설치 중…</span>}{error && <span role="status">{error} <button disabled={busy} onClick={() => void check(true)}>다시 시도</button></span>}
    {update && !offer && state === "available" && <button onClick={() => setOffer(true)}>업데이트 보기</button>}
    {offer && update && <div className="product-modal-backdrop"><section role="dialog" aria-modal="true" aria-label="MindCore 업데이트" className="product-modal"><h2>{state === "ready" ? "업데이트 설치 준비 완료" : "MindCore 업데이트가 있습니다"}</h2><p>현재 버전: {update.currentVersion}</p><p>새 버전: {update.version}</p><p>{state === "ready" ? "설치하면 MindCore가 다시 시작됩니다." : update.body?.replace(/[#*`]/g, "").slice(0, 240) || "새 버전의 업데이트 내용을 확인해 주세요."}</p><div className="product-modal-actions"><button disabled={busy} onClick={later}>나중에</button><button onClick={() => setNotes({ version: update.version, markdown: update.body || "상세 업데이트 내용을 불러오지 못했습니다." })}>업데이트 내용 보기</button><button disabled={busy} onClick={() => void (state === "ready" ? install() : download())}>{state === "ready" ? "설치 및 다시 시작" : "업데이트"}</button></div>{update.announcementUrl && safePublicUrl(update.announcementUrl) && <button onClick={() => void openPublicUrl(update.announcementUrl!).catch(() => undefined)}>원본 공지 보기</button>}</section></div>}
    {notes && <ReleaseNotesModal {...notes} onClose={() => setNotes(null)} />}</section>;
}

import { useEffect, useState } from "react";
import { loadDesktopUpdateService, updateErrorMessage, type UpdateInfo, type UpdateService } from "../services/updateService";

type UpdateState = "idle" | "checking" | "up-to-date" | "available" | "downloading" | "ready" | "installing" | "error";

function percent(downloadedBytes: number, contentLength?: number): string | null {
  if (!contentLength || contentLength <= 0) return null;
  return `${Math.min(100, Math.round((downloadedBytes / contentLength) * 100))}%`;
}

export function DesktopUpdater({ service: serviceOverride }: { service?: UpdateService }) {
  const [service, setService] = useState<UpdateService | null>(serviceOverride ?? null);
  const [version, setVersion] = useState<string | null>(null);
  const [state, setState] = useState<UpdateState>("idle");
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ downloadedBytes: number; contentLength?: number }>({ downloadedBytes: 0 });

  useEffect(() => {
    if (serviceOverride) return;
    void loadDesktopUpdateService().then(setService).catch(() => setError("Could not check for updates."));
  }, [serviceOverride]);

  useEffect(() => {
    if (!service) return;
    void service.getCurrentVersion().then(setVersion).catch(() => setVersion(null));
  }, [service]);

  const check = async () => {
    if (!service) return;
    setState("checking"); setError(null); setUpdate(null);
    try {
      const next = await service.check();
      if (next) { setUpdate(next); setState("available"); }
      else setState("up-to-date");
    } catch (reason) { setError(updateErrorMessage(reason, "check")); setState("error"); }
  };

  useEffect(() => {
    if (!service) return;
    const timer = window.setTimeout(() => void check(), 1500);
    return () => window.clearTimeout(timer);
  // The startup check intentionally runs once; it never blocks setup or chat.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [service]);

  const download = async () => {
    if (!update) return;
    setState("downloading"); setError(null); setProgress({ downloadedBytes: 0 });
    try { await update.download(setProgress); setState("ready"); }
    catch (reason) { setError(updateErrorMessage(reason, "download")); setState("error"); }
  };

  const install = async () => {
    if (!service || !update) return;
    setState("installing"); setError(null);
    try { await service.installAndRelaunch(update); }
    catch (reason) { setError(updateErrorMessage(reason, "install")); setState("error"); }
  };

  if (!service && !error) return null;
  const progressPercent = percent(progress.downloadedBytes, progress.contentLength);
  return <section className="desktop-updater" aria-label="MindCore updates">
    <span>MindCore{version ? ` · Version ${version}` : ""}</span>
    <button type="button" disabled={!service || state === "checking" || state === "downloading" || state === "installing"} onClick={() => void check()}>Check for Updates</button>
    {state === "up-to-date" && <p>MindCore is up to date.</p>}
    {state === "available" && update && <div className="update-dialog" role="dialog" aria-label="Update available"><p>MindCore {update.version} is available.</p><p>Current version: {update.currentVersion}</p>{update.body && <pre>{update.body}</pre>}<div><button type="button" onClick={() => { setUpdate(null); setState("idle"); }}>Later</button><button type="button" onClick={() => void download()}>Download Update</button></div></div>}
    {state === "downloading" && <p>Downloading update…{progressPercent ? ` ${progressPercent}` : ""}</p>}
    {state === "ready" && <div className="update-dialog" role="dialog" aria-label="Install update"><p>Download complete. MindCore will close its local backend before installing and restarting.</p><div><button type="button" onClick={() => setState("idle")}>Later</button><button type="button" onClick={() => void install()}>Install and Restart</button></div></div>}
    {state === "installing" && <p>Installing update…</p>}
    {error && <p className="workspace-muted">{error} <button type="button" onClick={() => void check()}>Retry</button></p>}
  </section>;
}

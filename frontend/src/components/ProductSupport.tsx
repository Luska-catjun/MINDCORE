import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { DesktopUpdater } from "./DesktopUpdater";
import { FeedbackDialog } from "./FeedbackDialog";
import { CURRENT_VERSION } from "../releaseNotes";
import type { Diagnostics } from "../services/productSupport";
export function ProductSupport({ ready, provider, connection, feedbackRequest = 0, updateRequest, updateSurface }: { updateSurface?: HTMLElement | null; ready: boolean; provider?: string; connection?: string; feedbackRequest?: number; updateRequest?: { id: number; action: "check" | "notes" } }) {
  const [feedback, setFeedback] = useState(false), [updaterEnabled, setUpdaterEnabled] = useState(false), [updateState, setUpdateState] = useState("idle");
  const [metadata, setMetadata] = useState<Diagnostics>({ platform: "desktop", app_version: CURRENT_VERSION });
  useEffect(() => { if (feedbackRequest > 0) setFeedback(true); }, [feedbackRequest]);
  useEffect(() => {
    void invoke<{ updater_available: boolean }>("get_runtime_capabilities").then(value => setUpdaterEnabled(value.updater_available)).catch(() => undefined);
    void invoke<Diagnostics>("get_product_support_metadata").then(value => setMetadata({ platform: value.platform, os_version: value.os_version, provider: value.provider, app_version: CURRENT_VERSION })).catch(() => undefined);
  }, []);
  return <><div className={updateSurface ? "workspace-support" : "setup-update-support"}><DesktopUpdater controlsTarget={updateSurface ?? undefined} request={updateRequest} enabled={updaterEnabled} autoCheck={ready} onStateChange={setUpdateState} /></div>{!updateSurface && <footer className="product-support-bar"><button onClick={() => setFeedback(true)}>피드백</button></footer>}{feedback && <FeedbackDialog diagnostics={{ ...metadata, ...(provider ? { provider } : {}), ...(connection ? { persona_connection: connection } : {}), update_state: updateState }} onClose={() => setFeedback(false)} />}</>;
}

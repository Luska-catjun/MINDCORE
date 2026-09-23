import { invoke } from "@tauri-apps/api/core";
import { isDesktopRuntime } from "./api/client";

const FRONTEND_STARTUP_OPERATIONS = new Set([
  "native_ready",
  "authenticated_session",
  "conversation_load_start",
  "conversation_load_end",
  "chat_history_start",
  "chat_history_end",
  "chat_ready",
]);

/** Production-safe startup trace: fixed labels and duration, no user or config data. */
export function emitFrontendStartupTiming(operation: string): void {
  if (!FRONTEND_STARTUP_OPERATIONS.has(operation)) return;
  const elapsedMs = Math.round(performance.now());
  console.info(
    `MINDCORE_STARTUP_TIMING phase=frontend operation=${operation} elapsed_ms=${elapsedMs}`,
  );
  if (isDesktopRuntime()) {
    void invoke("report_frontend_startup_stage", { operation, elapsedMs }).catch(() => undefined);
  }
}

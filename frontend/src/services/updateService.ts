import { isDesktopRuntime } from "../api/client";

export type UpdateProgress = { downloadedBytes: number; contentLength?: number };
export type UpdateInfo = {
  currentVersion: string;
  version: string;
  date?: string;
  body?: string;
  announcementUrl?: string;
  download: (onProgress: (progress: UpdateProgress) => void) => Promise<void>;
  install: () => Promise<void>;
};

export interface UpdateService {
  getCurrentVersion: () => Promise<string>;
  check: () => Promise<UpdateInfo | null>;
  installAndRelaunch: (update: UpdateInfo) => Promise<void>;
}

type NativeUpdate = {
  currentVersion: string;
  version: string;
  date?: string;
  body?: string;
  download: (onEvent: (event: { event: "Started"; data: { contentLength?: number } } | { event: "Progress"; data: { chunkLength: number } } | { event: "Finished" }) => void) => Promise<void>;
  install: () => Promise<void>;
};

export function createUpdateService(native: {
  getVersion: () => Promise<string>;
  check: () => Promise<NativeUpdate | null>;
  stopBackend: () => Promise<unknown>;
  startBackend: () => Promise<unknown>;
  relaunch: () => Promise<void>;
}): UpdateService {
  return {
    getCurrentVersion: native.getVersion,
    async check() {
      const update = await native.check();
      if (!update) return null;
      return {
        currentVersion: update.currentVersion,
        version: update.version,
        date: update.date,
        body: update.body,
        async download(onProgress) {
          let downloadedBytes = 0;
          await update.download((event) => {
            if (event.event === "Started") onProgress({ downloadedBytes, contentLength: event.data.contentLength });
            if (event.event === "Progress") {
              downloadedBytes += event.data.chunkLength;
              onProgress({ downloadedBytes });
            }
          });
        },
        install: () => update.install(),
      };
    },
    async installAndRelaunch(update) {
      await native.stopBackend();
      try {
        await update.install();
        await native.relaunch();
      } catch (error) {
        // An unsuccessful install must leave the existing application usable.
        await native.startBackend().catch(() => undefined);
        throw error;
      }
    },
  };
}

export async function loadDesktopUpdateService(): Promise<UpdateService | null> {
  if (!isDesktopRuntime()) return null;
  const [{ getVersion }, { check }, { relaunch }, { invoke }] = await Promise.all([
    import("@tauri-apps/api/app"),
    import("@tauri-apps/plugin-updater"),
    import("@tauri-apps/plugin-process"),
    import("@tauri-apps/api/core"),
  ]);
  return createUpdateService({
    getVersion,
    check,
    stopBackend: () => invoke("stop_mindcore_backend"),
    startBackend: () => invoke("start_mindcore_backend"),
    relaunch,
  });
}

export function updateErrorMessage(error: unknown, action: "check" | "download" | "install"): string {
  const detail = error instanceof Error ? error.message.toLowerCase() : "";
  if (/signature|verify|verification/.test(detail)) return "업데이트 파일을 확인할 수 없어 설치하지 않았습니다.";
  if (action === "check") return "업데이트를 확인할 수 없습니다.";
  if (action === "download") return "업데이트를 다운로드하지 못했습니다. 현재 앱은 그대로 사용할 수 있습니다.";
  return "업데이트를 설치하지 못했습니다. 현재 앱은 그대로 사용할 수 있습니다.";
}

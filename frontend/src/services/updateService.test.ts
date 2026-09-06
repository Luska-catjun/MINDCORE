import { describe, expect, it, vi } from "vitest";
import { createUpdateService, updateErrorMessage } from "./updateService";

const nativeUpdate = (overrides: Partial<{ download: () => Promise<void>; install: () => Promise<void> }> = {}) => ({
  currentVersion: "0.1.0", version: "0.1.1", body: "Plain text notes",
  download: async (onEvent: (event: { event: "Started"; data: { contentLength?: number } } | { event: "Progress"; data: { chunkLength: number } } | { event: "Finished" }) => void) => { onEvent({ event: "Started", data: { contentLength: 10 } }); onEvent({ event: "Progress", data: { chunkLength: 4 } }); onEvent({ event: "Progress", data: { chunkLength: 6 } }); },
  install: async () => undefined,
  ...overrides,
});

describe("desktop update service", () => {
  it("uses the native updater semver decision and reports byte progress without inventing percentages", async () => {
    const update = nativeUpdate();
    const service = createUpdateService({ getVersion: vi.fn().mockResolvedValue("0.1.0"), check: vi.fn().mockResolvedValue(update), stopBackend: vi.fn(), startBackend: vi.fn(), relaunch: vi.fn() });
    const available = await service.check();
    expect(available?.version).toBe("0.1.1");
    const progress: unknown[] = [];
    await available?.download((item) => progress.push(item));
    expect(progress).toEqual([{ downloadedBytes: 0, contentLength: 10 }, { downloadedBytes: 4 }, { downloadedBytes: 10 }]);
  });

  it("stops the existing sidecar before install and relaunches only after install", async () => {
    const order: string[] = [];
    const update = nativeUpdate({ install: async () => { order.push("install"); } });
    const service = createUpdateService({ getVersion: vi.fn(), check: vi.fn(), stopBackend: vi.fn(async () => { order.push("stop"); }), startBackend: vi.fn().mockResolvedValue(undefined), relaunch: vi.fn(async () => { order.push("relaunch"); throw new Error("relaunch exits"); }) });
    await expect(service.installAndRelaunch({ currentVersion: "0.1.0", version: "0.1.1", download: async () => undefined, install: update.install })).rejects.toThrow("relaunch exits");
    expect(order).toEqual(["stop", "install", "relaunch"]);
  });

  it("keeps the native update receiver through download and install", async () => {
    const native = {
      currentVersion: "0.1.0", version: "0.1.1",
      downloaded: false,
      async download() { this.downloaded = true; },
      async install() {
        if (!this.downloaded) throw new Error("Update.install called before Update.download");
      },
    };
    const service = createUpdateService({ getVersion: vi.fn(), check: vi.fn().mockResolvedValue(native), stopBackend: vi.fn(), startBackend: vi.fn(), relaunch: vi.fn() });
    const update = await service.check();
    await update?.download(() => undefined);
    await expect(update && service.installAndRelaunch(update)).resolves.toBeUndefined();
  });

  it("restores the local backend if installation fails", async () => {
    const order: string[] = [];
    const startBackend = vi.fn(async () => { order.push("restart"); });
    const service = createUpdateService({ getVersion: vi.fn(), check: vi.fn(), stopBackend: vi.fn(async () => { order.push("stop"); }), startBackend, relaunch: vi.fn() });
    await expect(service.installAndRelaunch({ currentVersion: "0.1.0", version: "0.1.1", download: async () => undefined, install: async () => { order.push("install"); throw new Error("signature verification failed"); } })).rejects.toThrow("signature");
    expect(startBackend).toHaveBeenCalledOnce();
    expect(order).toEqual(["stop", "install", "restart"]);
  });

  it("keeps verification failures hard and update errors non-fatal", () => {
    expect(updateErrorMessage(new Error("signature verification failed"), "download")).toBe("The update could not be verified and was not installed.");
    expect(updateErrorMessage(new Error("offline"), "check")).toBe("Could not check for updates.");
  });
});

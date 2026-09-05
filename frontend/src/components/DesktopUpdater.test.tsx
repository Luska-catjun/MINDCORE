import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopUpdater } from "./DesktopUpdater";
import type { UpdateService } from "../services/updateService";

const update = { currentVersion: "0.1.0", version: "0.1.1", body: "<b>Release notes remain plain text</b>", download: vi.fn(), install: vi.fn() };
const service: UpdateService = { getCurrentVersion: vi.fn(), check: vi.fn(), installAndRelaunch: vi.fn() };

describe("DesktopUpdater", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    service.getCurrentVersion = vi.fn().mockResolvedValue("0.1.0");
    service.check = vi.fn().mockResolvedValue(null);
    service.installAndRelaunch = vi.fn().mockResolvedValue(undefined);
    update.download.mockImplementation(async (progress) => progress({ downloadedBytes: 50, contentLength: 100 }));
    update.install.mockResolvedValue(undefined);
  });

  it("shows the native package version and the up-to-date state", async () => {
    render(<DesktopUpdater service={service} />);
    await waitFor(() => expect(screen.getByText(/Version 0.1.0/)).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "Check for Updates" }));
    expect(await screen.findByText("MindCore is up to date.")).toBeTruthy();
  });

  it("shows plain-text release notes, progress, then installs through the lifecycle service", async () => {
    service.check = vi.fn().mockResolvedValue(update);
    render(<DesktopUpdater service={service} />);
    await userEvent.click(screen.getByRole("button", { name: "Check for Updates" }));
    expect(await screen.findByText("MindCore 0.1.1 is available.")).toBeTruthy();
    expect(screen.getByText("<b>Release notes remain plain text</b>")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Download Update" }));
    expect(await screen.findByText(/Download complete/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Install and Restart" }));
    await waitFor(() => expect(service.installAndRelaunch).toHaveBeenCalledWith(update));
  });

  it("keeps update errors non-fatal and offers retry", async () => {
    service.check = vi.fn().mockRejectedValue(new Error("offline"));
    render(<DesktopUpdater service={service} />);
    await userEvent.click(screen.getByRole("button", { name: "Check for Updates" }));
    expect(await screen.findByText("Could not check for updates.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});

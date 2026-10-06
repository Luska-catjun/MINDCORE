import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopUpdater } from "./DesktopUpdater";
import type { UpdateService } from "../services/updateService";
const update = { currentVersion: "0.4.0", version: "0.4.1", body: "## 변경 사항\n\n**안전 개선**\n\n[공지](https://example.com/post)", download: vi.fn(), install: vi.fn() };
const service: UpdateService = { getCurrentVersion: vi.fn(), check: vi.fn(), installAndRelaunch: vi.fn() };
describe("DesktopUpdater", () => {
 beforeEach(() => { vi.clearAllMocks(); service.getCurrentVersion = vi.fn().mockResolvedValue("0.4.0"); service.check = vi.fn().mockResolvedValue(null); service.installAndRelaunch = vi.fn().mockResolvedValue(undefined); update.download.mockImplementation(async progress => progress({ downloadedBytes: 100, contentLength: 100 })); });
 it("shows current bundled notes offline and native package version", async () => {
  render(<DesktopUpdater service={service} autoCheck={false} />); await screen.findByText(/버전 0.4.0/);
  await userEvent.click(screen.getByRole("button", { name: "업데이트 내용" })); expect(screen.getByRole("dialog", {name: "업데이트 내용"})).toBeTruthy(); expect(screen.getByRole("heading", {name: "MindCore 0.4.0"})).toBeTruthy();
 });
 it("same version does not show a recommendation", async () => {
  service.check = vi.fn().mockResolvedValue({...update, version: "0.4.0"}); render(<DesktopUpdater service={service} autoCheck={false} />);
  await userEvent.click(screen.getByRole("button", {name: "업데이트 확인"})); expect(await screen.findByText("최신 버전을 사용 중입니다.")).toBeTruthy(); expect(screen.queryByRole("dialog")).toBeNull();
 });
 it("new version offers Later, details and existing download/install handoff", async () => {
  service.check = vi.fn().mockResolvedValue(update); render(<DesktopUpdater service={service} autoCheck={false} />);
  await userEvent.click(screen.getByRole("button", {name: "업데이트 확인"})); expect(await screen.findByText("새 버전: 0.4.1")).toBeTruthy(); expect(update.download).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", {name: "업데이트 내용 보기"})); expect(screen.getByText("안전 개선")).toBeTruthy(); expect(screen.getByRole("link", {name: "공지"})).toBeTruthy();
  await userEvent.click(screen.getByRole("button", {name: "닫기"})); await userEvent.click(screen.getByRole("button", {name: "업데이트"}));
  await screen.findByText("업데이트 설치 준비 완료"); await userEvent.click(screen.getByRole("button", {name: "설치 및 다시 시작"})); await waitFor(() => expect(service.installAndRelaunch).toHaveBeenCalledWith(update));
 });
 it("Later closes without immediate popup and manual check can offer again", async () => {
  service.check = vi.fn().mockResolvedValue({...update, version:"0.4.2"}); const result = render(<DesktopUpdater service={service} autoCheck={false} />);
  await userEvent.click(screen.getByRole("button", {name:"업데이트 확인"})); await screen.findByRole("dialog"); await userEvent.click(screen.getByRole("button", {name:"나중에"}));
  result.rerender(<DesktopUpdater service={service} autoCheck={false} />); expect(screen.queryByRole("dialog")).toBeNull(); expect(update.download).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", {name:"업데이트 확인"})); expect(await screen.findByRole("dialog")).toBeTruthy();
 });
 it("missing remote notes retains the offer and shows safe fallback", async () => {
  service.check = vi.fn().mockResolvedValue({...update, body:undefined}); render(<DesktopUpdater service={service} autoCheck={false} />); await userEvent.click(screen.getByRole("button", {name:"업데이트 확인"})); await screen.findByRole("dialog");
  await userEvent.click(screen.getByRole("button", {name:"업데이트 내용 보기"})); expect(screen.getByText("상세 업데이트 내용을 불러오지 못했습니다.")).toBeTruthy();
 });
 it("automatically recommends a new version after readiness without installing", async () => {
  service.check = vi.fn().mockResolvedValue({...update, version:"0.4.9"}); render(<DesktopUpdater service={service} />);
  expect(await screen.findByRole("dialog",{name:"MindCore 업데이트"},{timeout:4000})).toBeTruthy(); expect(update.download).not.toHaveBeenCalled();
 });
 it("check failure is nonfatal and allows retry", async () => {
  service.check = vi.fn().mockRejectedValue(new Error("private-token")); render(<DesktopUpdater service={service} autoCheck={false} />); await userEvent.click(screen.getByRole("button", {name:"업데이트 확인"})); expect(await screen.findByText("업데이트를 확인할 수 없습니다.")).toBeTruthy(); expect(screen.queryByText("private-token")).toBeNull(); expect(screen.getByRole("button",{name:"다시 시도"})).toBeTruthy();
 });
});

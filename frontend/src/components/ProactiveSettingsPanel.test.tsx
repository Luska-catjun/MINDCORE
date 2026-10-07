import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
import { ProactiveSettingsPanel } from "./ProactiveSettingsPanel";
const policy = { enabled: false, cooldown_seconds: 1901, quiet_hours_enabled: true, quiet_start: "22:15", quiet_end: "08:45" };
describe("existing proactive policy in product settings", () => {
 beforeEach(() => { vi.clearAllMocks(); invoke.mockResolvedValue(policy); });
  it("loads and saves proactive policy for the active Persona profile", async () => {
    const changed = vi.fn().mockResolvedValue(undefined);
    invoke.mockImplementation((command: string, payload?: { settings?: unknown }) => {
      if (command === "get_proactive_settings") return Promise.resolve({
        enabled: false, cooldown_seconds: 1800, quiet_hours_enabled: true,
        quiet_start: "23:00", quiet_end: "07:00",
      });
      if (command === "update_proactive_settings") return Promise.resolve(payload?.settings);
      return Promise.resolve();
    });
    render(<ProactiveSettingsPanel personaId="persona-a" onSaved={changed} />);

    expect(await screen.findByText("MindCore가 실행 중일 때, 적절한 시점에 현재 Persona가 대화를 시작할 수 있습니다.")).toBeTruthy();
    await userEvent.click(screen.getByLabelText("먼저 말 걸기 사용"));
    fireEvent.change(screen.getByLabelText("최소 메시지 간격 (초)"), { target: { value: "3600" } });
    await userEvent.click(screen.getByRole("button", { name: "설정 저장" }));

    expect(invoke).toHaveBeenCalledWith("update_proactive_settings", {
      personaId: "persona-a",
      settings: {
        enabled: true, cooldown_seconds: 3600, quiet_hours_enabled: true,
        quiet_start: "23:00", quiet_end: "07:00",
      },
    });
    expect(changed).toHaveBeenCalledOnce();
  });
 it("loads exact persisted values, quiet hours and status; saves and loads again after remount", async () => {
   let persisted = policy;
   invoke.mockImplementation((command, args) => { if(command === "update_proactive_settings") persisted=args.settings; return Promise.resolve(persisted); });
   const view=render(<ProactiveSettingsPanel personaId="a" onSaved={vi.fn().mockResolvedValue(undefined)}/>);
   await screen.findByDisplayValue("1901"); expect(screen.getByDisplayValue("22:15")).toBeTruthy(); expect(screen.getByDisplayValue("08:45")).toBeTruthy(); expect(screen.getByText("비활성")).toBeTruthy();
   await userEvent.click(screen.getByLabelText("먼저 말 걸기 사용")); fireEvent.change(screen.getByLabelText("조용한 시간 시작"),{target:{value:"21:30"}}); await userEvent.click(screen.getByRole("button",{name:"설정 저장"})); await screen.findByText("설정을 저장했습니다.");
   view.unmount(); render(<ProactiveSettingsPanel personaId="a" onSaved={vi.fn().mockResolvedValue(undefined)}/>);
   expect(await screen.findByDisplayValue("21:30")).toBeTruthy(); expect((screen.getByLabelText("먼저 말 걸기 사용") as HTMLInputElement).checked).toBe(true);
 });
 it("rejects stale policy response after Persona changes and never invents default controls on load failure", async () => {
   let finish!: (value: typeof policy)=>void; invoke.mockImplementation((_command,args)=>args.personaId === "a" ? new Promise(resolve=>{finish=resolve;}) : Promise.reject(new Error("unavailable")));
   const view=render(<ProactiveSettingsPanel personaId="a" onSaved={vi.fn()}/>); view.rerender(<ProactiveSettingsPanel personaId="b" onSaved={vi.fn()}/>);
   await screen.findByRole("alert"); finish(policy); await waitFor(()=>expect(screen.queryByLabelText("먼저 말 걸기 사용")).toBeNull());
 });
 it("retains draft on native validation failure and uses fixed system notification settings entry", async () => {
   invoke.mockImplementation(command=>command === "update_proactive_settings" ? Promise.reject(new Error("invalid")) : Promise.resolve(policy));
   render(<ProactiveSettingsPanel personaId="a" onSaved={vi.fn()}/>); await screen.findByDisplayValue("1901"); fireEvent.change(screen.getByLabelText("최소 메시지 간격 (초)"),{target:{value:"299"}});
   await userEvent.click(screen.getByRole("button",{name:"설정 저장"})); await screen.findByRole("alert"); expect(screen.getByDisplayValue("299")).toBeTruthy();
   await userEvent.click(screen.getByRole("button",{name:"시스템 알림 설정 열기"})); expect(invoke).toHaveBeenCalledWith("open_notification_settings"); expect(screen.queryByText(/시스템 알림 권한:/)).toBeNull();
 });
});

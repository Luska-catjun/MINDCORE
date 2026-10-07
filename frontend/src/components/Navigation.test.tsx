import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { DATA_GROUPS, DataNavigation, type DataView } from "./DataNavigation";
import { AppSettings } from "./AppSettings";
describe("product information architecture", () => {
  it("makes all fifteen existing domains reachable through compact secondary navigation", () => {
    const select = vi.fn();
    const { rerender } = render(<DataNavigation view="messages" onChange={select} />);
    expect(DATA_GROUPS.flatMap(group => group.items).length).toBe(15);
    for (const group of DATA_GROUPS) {
      fireEvent.click(within(screen.getByRole("navigation", { name: "데이터 분류" })).getByRole("button", { name: group.label }));
      expect(select).toHaveBeenLastCalledWith(group.items[0].id);
      rerender(<DataNavigation view={group.items[0].id} onChange={select} />);
      for (const item of group.items) { fireEvent.click(screen.getByRole("button", { name: item.label })); expect(select).toHaveBeenLastCalledWith(item.id); }
    }
  });
  it("remembers the selected subtab of each data group", () => {
    function Shell() { const [view, setView] = useState<DataView>("messages"); return <DataNavigation view={view} onChange={setView} />; }
    render(<Shell />); fireEvent.click(screen.getByRole("button", { name: "기억" })); fireEvent.click(screen.getByRole("button", { name: "에피소드" }));
    fireEvent.click(screen.getByRole("button", { name: "진단" })); fireEvent.click(screen.getByRole("button", { name: "기억" }));
    expect(screen.getByRole("button", { name: "에피소드" }).getAttribute("aria-current")).toBe("page");
  });
  it("keeps Persona controls, connection, configuration, notes and update entry points in settings", () => {
    const configure = vi.fn(), open = vi.fn();
    render(<AppSettings personaControls={<button>Persona 관리</button>} connection={<div>Persona Connection</div>} onConfigure={configure} onOpenConfiguration={open} proactive={<p>현재 Persona 먼저 말 걸기</p>} />);
    expect(screen.getByRole("button", { name: "Persona 관리" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "연결" })); expect(screen.getByText("Persona Connection")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "AI 설정" })); fireEvent.click(screen.getByRole("button", { name: "현재 Persona 재설정" })); expect(configure).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "업데이트" })); expect(document.getElementById("settings-update-surface")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "먼저 말 걸기" })); expect(screen.getByText("현재 Persona 먼저 말 걸기")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "일반" })); fireEvent.click(screen.getByRole("button", { name: "설정 폴더 열기" })); expect(open).toHaveBeenCalledOnce();
  });
});

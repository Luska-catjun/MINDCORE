import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

describe("Sidebar proactive unread badge", () => {
  it("shows unread proactive count on Chat and omits it at zero", () => {
    const props = {
      activeSection: "data" as const,
      onSectionChange: vi.fn(),
      isOpen: false,
      onClose: vi.fn(),
      backendStatus: "connected" as const,
    };
    const { rerender } = render(<Sidebar {...props} unreadCount={2} />);
    expect(screen.getByLabelText("2 unread proactive messages")).toBeTruthy();
    rerender(<Sidebar {...props} unreadCount={0} />);
    expect(screen.queryByLabelText(/unread proactive messages/)).toBeNull();
  });

  it("exposes only four product entries and removes domain navigation", () => {
    render(<Sidebar activeSection="chat" onSectionChange={vi.fn()} isOpen={false} onClose={vi.fn()} backendStatus="connected" />);
    expect(screen.getAllByRole("button").map(item => item.textContent)).toEqual(["대화", "데이터 관리", "앱 설정", "피드백"]);
    expect(screen.queryByRole("button", { name: "Persona Connection" })).toBeNull();
    expect(screen.queryByRole("button", { name: /sync|devices/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Debug" })).toBeNull();
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

describe("Sidebar proactive unread badge", () => {
  it("shows unread proactive count on Chat and omits it at zero", () => {
    const props = {
      activeView: "messages" as const,
      onViewChange: vi.fn(),
      isOpen: false,
      onClose: vi.fn(),
      backendStatus: "connected" as const,
    };
    const { rerender } = render(<Sidebar {...props} unreadCount={2} />);
    expect(screen.getByLabelText("2 unread proactive messages")).toBeTruthy();
    rerender(<Sidebar {...props} unreadCount={0} />);
    expect(screen.queryByLabelText(/unread proactive messages/)).toBeNull();
  });

  it("exposes Persona Connection and removes deprecated sync navigation", () => {
    render(<Sidebar activeView="chat" onViewChange={vi.fn()} isOpen={false} onClose={vi.fn()} backendStatus="connected" />);
    expect(screen.getByRole("button", { name: "Persona Connection" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /sync|devices/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Debug" })).toBeNull();
  });
});

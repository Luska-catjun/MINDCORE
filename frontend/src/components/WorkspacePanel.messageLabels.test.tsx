import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ observeMessages: vi.fn(), deleteMessage: vi.fn() }));
vi.mock("../api/client", () => ({ api: apiMock, ApiError: class extends Error {} }));
import { WorkspacePanel } from "./WorkspacePanel";

const rows = ["diana", "user"].map((role) => Object.freeze({
  id: `${role}-historical`, conversation_id: "historical-conversation", role,
  content: `Historical ${role} content`, created_at: "2020-01-01T00:00:00Z",
}));
const props = { view: "messages" as const, backendStatus: "connected" as const,
  onToggleSidebar: () => {}, personaDisplayName: "신데렐라", userDisplayName: "Synthetic Configured User" };

describe("Data Management message presentation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.observeMessages.mockResolvedValue({ items: rows, total: 2, limit: 100, offset: 0, query_latency_ms: 1 });
  });

  it("maps historical rows and deletion inspection, updating even an open dialog on switch", async () => {
    const snapshot = JSON.stringify(rows);
    const { rerender } = render(<WorkspacePanel {...props} />);
    await screen.findByText("신데렐라");
    expect(screen.getByText(props.userDisplayName)).toBeTruthy();
    expect(screen.queryByText("diana")).toBeNull();
    expect(screen.queryByText("user")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    expect(within(screen.getByRole("dialog")).getByText(/^신데렐라 · Message/)).toBeTruthy();

    rerender(<WorkspacePanel {...props} personaDisplayName="Synthetic Second Persona" userDisplayName="Synthetic New User" />);
    expect(screen.getByText("Synthetic Second Persona")).toBeTruthy();
    expect(within(screen.getByRole("dialog")).getByText(/^Synthetic Second Persona · Message/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    expect(within(screen.getByRole("dialog")).getByText(/^Synthetic New User · Message/)).toBeTruthy();
    expect(JSON.stringify(rows)).toBe(snapshot);
    expect(rows.map(row => row.role)).toEqual(["diana", "user"]);
    expect(apiMock.deleteMessage).not.toHaveBeenCalled();
  });

  it("uses Persona/User fallbacks when authoritative names are missing", async () => {
    render(<WorkspacePanel {...props} personaDisplayName=" " userDisplayName="" />);
    expect(await screen.findByText("Persona")).toBeTruthy();
    expect(screen.getByText("User")).toBeTruthy();
  });
});

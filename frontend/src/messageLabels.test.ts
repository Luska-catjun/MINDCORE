import chatSource from "./components/ChatWindow.tsx?raw";
import bubbleSource from "./components/MessageBubble.tsx?raw";
import workspaceSource from "./components/WorkspacePanel.tsx?raw";
import { describe, expect, it } from "vitest";
import { messageRoleLabel } from "./messageLabels";

describe("message label presentation boundary", () => {
  it("never returns an unknown compatibility token as the label", () => {
    expect(messageRoleLabel("unknown-role", "신데렐라", "Synthetic Configured User")).toBe("Message");
    expect(messageRoleLabel("diana", null, null)).toBe("Persona");
    expect(messageRoleLabel("user", null, null)).toBe("User");
    expect(messageRoleLabel("system")).toBe("System");
    expect(messageRoleLabel("tool")).toBe("Tool");
  });

  it("keeps raw roles and test names out of production renderer labels", () => {
    for (const source of [chatSource, bubbleSource, workspaceSource]) {
      expect(source).not.toMatch(/\{\s*(?:message|item)\.role\s*\}/);
      expect(source).not.toMatch(/\{\s*["'](?:diana|user)["']\s*\}/);
      expect(source).not.toMatch(/>\s*(?:diana|user)\s*</);
      expect(source).not.toContain("신데렐라");
      expect(source).not.toContain("Synthetic Configured User");
    }
  });
});

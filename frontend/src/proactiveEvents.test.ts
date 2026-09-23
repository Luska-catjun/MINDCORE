import { describe, expect, it } from "vitest";
import {
  proactiveDeliveryMode,
  proactiveRouteTarget,
  rememberProactiveEvent,
  shouldMarkProactiveUnread,
  shouldSendProactiveOsNotification,
  proactiveUnreadKey,
} from "./proactiveEvents";

describe("proactive event delivery", () => {
  it("shows a bubble/toast without unread or OS notification in the focused conversation", () => {
    const mode = proactiveDeliveryMode({ conversationId: "c1", eventConversationId: "c1", activeView: "chat", visible: true, focused: true });
    expect(mode).toBe("visible_conversation");
    expect(shouldMarkProactiveUnread(mode)).toBe(false);
    expect(shouldSendProactiveOsNotification(mode)).toBe(false);
  });

  it("marks unread and uses in-app feedback in another foreground view", () => {
    const mode = proactiveDeliveryMode({ conversationId: "c1", eventConversationId: "c2", activeView: "messages", visible: true, focused: true });
    expect(mode).toBe("foreground_other");
    expect(shouldMarkProactiveUnread(mode)).toBe(true);
    expect(shouldSendProactiveOsNotification(mode)).toBe(false);
  });

  it("uses a privacy-safe OS notification while hidden or unfocused", () => {
    const mode = proactiveDeliveryMode({ conversationId: "c1", eventConversationId: "c2", activeView: "chat", visible: false, focused: false });
    expect(mode).toBe("background");
    expect(shouldMarkProactiveUnread(mode)).toBe(true);
    expect(shouldSendProactiveOsNotification(mode)).toBe(true);
  });

  it("deduplicates event message ids and routes to the originating Persona/conversation", () => {
    const seen = new Set<string>();
    expect(rememberProactiveEvent(seen, "m1")).toBe(true);
    expect(rememberProactiveEvent(seen, "m1")).toBe(false);
    expect(proactiveRouteTarget({ message_id: "m1", conversation_id: "cA", persona_id: "pA", created_at: "2026-01-01T00:00:00Z" }))
      .toEqual({ personaId: "pA", conversationId: "cA" });
  });

  it("keeps unread state scoped when Personas contain the same conversation id", () => {
    expect(proactiveUnreadKey("persona-a", "conversation-1"))
      .not.toBe(proactiveUnreadKey("persona-b", "conversation-1"));
  });
});

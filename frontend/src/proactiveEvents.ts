import type { ProactiveEvent } from "./types/api";

export type ProactiveDeliveryMode = "visible_conversation" | "foreground_other" | "background";

export function proactiveDeliveryMode(input: {
  conversationId: string | null;
  eventConversationId: string;
  activeView: string;
  visible: boolean;
  focused: boolean;
}): ProactiveDeliveryMode {
  if (!input.visible || !input.focused) return "background";
  if (input.activeView === "chat" && input.conversationId === input.eventConversationId) {
    return "visible_conversation";
  }
  return "foreground_other";
}

export function shouldMarkProactiveUnread(mode: ProactiveDeliveryMode): boolean {
  return mode !== "visible_conversation";
}

export function shouldSendProactiveOsNotification(mode: ProactiveDeliveryMode): boolean {
  return mode === "background";
}

export function rememberProactiveEvent(seen: Set<string>, messageId: string): boolean {
  if (seen.has(messageId)) return false;
  seen.add(messageId);
  if (seen.size > 500) {
    const recent = [...seen].slice(-250);
    seen.clear();
    recent.forEach((id) => seen.add(id));
  }
  return true;
}

export function proactiveRouteTarget(event: ProactiveEvent): {
  personaId: string;
  conversationId: string;
} {
  return { personaId: event.persona_id, conversationId: event.conversation_id };
}

export function proactiveUnreadKey(personaId: string, conversationId: string): string {
  return `${personaId}:${conversationId}`;
}

import type { LocalMessage } from "./chatHistory";

type MessageSummary = Array<{ id: string; sequence: number | null }>;

interface ChatDebugSnapshot {
  lastEvent: string;
  conversationId: string | null;
  revision?: number;
  cache?: MessageSummary;
  chatWindowProps?: MessageSummary;
  rendered?: MessageSummary;
  lastDurableFetch?: MessageSummary;
  lastFetch?: {
    status: "applied" | "discarded";
    requestRevision: number;
    currentRevision: number;
  };
}

declare global {
  interface Window {
    __DIANA_CHAT_DEBUG__?: ChatDebugSnapshot;
  }
}

function summarize(messages: LocalMessage[]): MessageSummary {
  return messages.map(({ id, sequence }) => ({ id, sequence }));
}

export function chatDebug(
  event: string,
  options: {
    conversationId: string | null;
    messages?: LocalMessage[];
    revision?: number;
    requestRevision?: number;
    currentRevision?: number;
  },
): void {
  if (typeof window === "undefined") return;

  const messages = options.messages ? summarize(options.messages) : undefined;
  const snapshot: ChatDebugSnapshot = window.__DIANA_CHAT_DEBUG__ ?? {
    lastEvent: "initial",
    conversationId: options.conversationId,
  };
  snapshot.lastEvent = event;
  snapshot.conversationId = options.conversationId;
  if (options.revision !== undefined) snapshot.revision = options.revision;

  if (event === "app_cache" || event === "before_send" || event === "optimistic_user_added" || event === "durable_user_assistant_merged") {
    snapshot.cache = messages;
  } else if (event === "chatwindow_props") {
    snapshot.chatWindowProps = messages;
  } else if (event === "render") {
    snapshot.rendered = messages;
  } else if (event === "durable_fetch") {
    snapshot.lastDurableFetch = messages;
  } else if (event === "fetch_applied" || event === "fetch_discarded") {
    snapshot.lastFetch = {
      status: event === "fetch_applied" ? "applied" : "discarded",
      requestRevision: options.requestRevision ?? 0,
      currentRevision: options.currentRevision ?? 0,
    };
  }

  window.__DIANA_CHAT_DEBUG__ = snapshot;
  console.debug(`[CHAT_DEBUG] ${event}`, {
    conversation: options.conversationId,
    count: messages?.length,
    messages,
    revision: options.revision,
    request_revision: options.requestRevision,
    current_revision: options.currentRevision,
  });
}

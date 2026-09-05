import type { MessageRead } from "./types/api";

export type LocalMessage = MessageRead & { _pending?: boolean; _failed?: boolean };

export interface ChatHistoryEntry {
  messages: LocalMessage[];
  /** Incremented for local/optimistic changes so an older fetch cannot replace them. */
  revision: number;
}

export type ChatHistoryCache = Record<string, ChatHistoryEntry>;

export function updateChatHistory(
  cache: ChatHistoryCache,
  conversationId: string,
  updater: (messages: LocalMessage[]) => LocalMessage[],
): ChatHistoryCache {
  const current = cache[conversationId] ?? { messages: [], revision: 0 };
  return {
    ...cache,
    [conversationId]: {
      messages: updater(current.messages),
      revision: current.revision + 1,
    },
  };
}

/**
 * A response only replaces the cache when no newer local mutation happened
 * after that request began. Durable history is otherwise refreshed next time
 * Chat mounts, without dropping an optimistic/recent message in the meantime.
 */
export function replaceDurableChatHistory(
  cache: ChatHistoryCache,
  conversationId: string,
  messages: LocalMessage[],
  expectedRevision: number,
): ChatHistoryCache {
  const current = cache[conversationId] ?? { messages: [], revision: 0 };
  if (current.revision !== expectedRevision) return cache;
  return {
    ...cache,
    [conversationId]: { ...current, messages },
  };
}

import type { MessageRead } from "./types/api";

export type LocalMessage = MessageRead & { _pending?: boolean; _failed?: boolean };

export interface ChatHistoryEntry {
  messages: LocalMessage[];
  /** Incremented for local/optimistic changes so an older fetch cannot replace them. */
  revision: number;
}

export type ChatHistoryCache = Record<string, ChatHistoryEntry>;

export interface PendingChatSend {
  conversationId: string;
  tempId: string;
  sessionGeneration: number;
}

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

/**
 * Reconcile the authoritative POST response independently of the optimistic
 * message's lifetime. A remount fetch may already have replaced the temporary
 * row with either or both durable rows, so durable ids are the dedupe authority.
 */
export function reconcileChatResponse(
  messages: LocalMessage[],
  conversationId: string,
  tempId: string,
  durableMessages: LocalMessage[],
): LocalMessage[] {
  const validDurableMessages = durableMessages.filter(
    (message) => message.conversation_id === conversationId,
  );
  if (validDurableMessages.length !== durableMessages.length) return messages;

  const durableIds = new Set(validDurableMessages.map((message) => message.id));
  const firstReplacementIndex = messages.findIndex(
    (message) => message.id === tempId || durableIds.has(message.id),
  );
  const retained = messages.filter(
    (message) => message.id !== tempId && !durableIds.has(message.id),
  );
  const insertionIndex = firstReplacementIndex < 0
    ? retained.length
    : messages
      .slice(0, firstReplacementIndex)
      .filter((message) => message.id !== tempId && !durableIds.has(message.id))
      .length;

  return [
    ...retained.slice(0, insertionIndex),
    ...validDurableMessages,
    ...retained.slice(insertionIndex),
  ];
}

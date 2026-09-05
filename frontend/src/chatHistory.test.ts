import { describe, expect, it } from "vitest";
import {
  replaceDurableChatHistory,
  updateChatHistory,
  type ChatHistoryCache,
} from "./chatHistory";
import type { MessageRead } from "./types/api";

const message = (id: string, conversationId = "x"): MessageRead => ({
  id,
  conversation_id: conversationId,
  role: id === "b" ? "diana" : "user",
  content: id.toUpperCase(),
  source_device: "test",
  sequence: null,
  metadata: {},
  timestamp: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
});

describe("chat history cache", () => {
  it("keeps histories for multiple conversations across view changes", () => {
    let cache: ChatHistoryCache = {};
    cache = updateChatHistory(cache, "x", () => [message("a", "x"), message("b", "x")]);
    cache = updateChatHistory(cache, "y", () => [message("c", "y")]);

    expect(cache.x.messages.map((item) => item.id)).toEqual(["a", "b"]);
    expect(cache.y.messages.map((item) => item.id)).toEqual(["c"]);
  });

  it("does not allow a stale durable fetch to overwrite a newly sent message", () => {
    let cache: ChatHistoryCache = {};
    cache = updateChatHistory(cache, "x", () => [message("a")]);
    const revisionBeforeSend = cache.x.revision;
    cache = updateChatHistory(cache, "x", (current) => [...current, message("c")]);

    const afterStaleFetch = replaceDurableChatHistory(cache, "x", [message("a")], revisionBeforeSend);
    expect(afterStaleFetch.x.messages.map((item) => item.id)).toEqual(["a", "c"]);
  });
});

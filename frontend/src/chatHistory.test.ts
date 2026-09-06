import { describe, expect, it } from "vitest";
import {
  reconcileChatResponse,
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

  it.each([
    ["optimistic placeholder", [message("a"), { ...message("temp-c"), _pending: true }]],
    ["no placeholder", [message("a"), message("b")]],
    ["durable user already fetched", [message("a"), message("b"), message("c")]],
    ["both durable messages already fetched", [message("a"), message("b"), message("c"), message("d")]],
  ])("reconciles a response by durable id with %s", (_case, current) => {
    const reconciled = reconcileChatResponse(
      current,
      "x",
      "temp-c",
      [message("c"), message("d")],
    );

    expect(reconciled.map((item) => item.id)).toEqual(
      current.some((item) => item.id === "b") ? ["a", "b", "c", "d"] : ["a", "c", "d"],
    );
    expect(reconciled.filter((item) => item.id === "c")).toHaveLength(1);
    expect(reconciled.filter((item) => item.id === "d")).toHaveLength(1);
    expect(reconciled.some((item) => item.id === "temp-c")).toBe(false);
  });

  it("updates only the pending conversation and rejects cross-conversation response rows", () => {
    let cache: ChatHistoryCache = {
      x: { messages: [message("a", "x"), { ...message("temp-c", "x"), _pending: true }], revision: 1 },
      y: { messages: [message("y-a", "y")], revision: 1 },
    };

    cache = updateChatHistory(cache, "x", (messages) => (
      reconcileChatResponse(messages, "x", "temp-c", [message("c", "x"), message("d", "x")])
    ));
    const rejected = reconcileChatResponse(cache.x.messages, "x", "temp-c", [message("c", "y"), message("d", "y")]);

    expect(cache.x.messages.map((item) => item.id)).toEqual(["a", "c", "d"]);
    expect(cache.y.messages.map((item) => item.id)).toEqual(["y-a"]);
    expect(rejected).toBe(cache.x.messages);
  });
});

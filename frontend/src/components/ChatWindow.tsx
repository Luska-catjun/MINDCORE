// src/components/ChatWindow.tsx

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { LocalMessage, PendingChatSend } from "../chatHistory";
import type { ChatResponse } from "../types/api";
import { chatDebug } from "../chatDebug";
import { MessageBubble } from "./MessageBubble";
import { MessageInput } from "./MessageInput";
import { PersonaAvatar } from "./PersonaAvatar";

interface ChatWindowProps {
  personaDisplayName: string;
  personaId: string | null;
  personaAvatarExtension?: string | null;
  conversationId: string | null;
  loadingConversation: boolean;
  onToggleSidebar: () => void;
  sourceDevice: string;
  onStateUpdated: () => void;
  messages: LocalMessage[];
  historyRevision: number;
  sending: boolean;
  onDurableMessagesLoaded: (conversationId: string, messages: LocalMessage[], expectedRevision: number) => void;
  onSendStarted: (conversationId: string, optimistic: LocalMessage) => PendingChatSend;
  onSendSucceeded: (send: PendingChatSend, reply: ChatResponse) => boolean;
  onSendFailed: (send: PendingChatSend) => boolean;
}

export function ChatWindow({
  personaDisplayName,
  personaId,
  personaAvatarExtension,
  conversationId,
  loadingConversation,
  onToggleSidebar,
  sourceDevice,
  onStateUpdated,
  messages,
  historyRevision,
  sending,
  onDurableMessagesLoaded,
  onSendStarted,
  onSendSucceeded,
  onSendFailed,
}: ChatWindowProps) {
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const sendingRef = useRef(sending);
  sendingRef.current = sending;
  const historyRef = useRef({ historyRevision, onDurableMessagesLoaded });
  historyRef.current = { historyRevision, onDurableMessagesLoaded };

  useEffect(() => {
    if (!conversationId) {
      return;
    }

    let cancelled = false;
    const expectedRevision = historyRef.current.historyRevision;
    setLoading(true);
    setLoadError(null);

    api
      .listMessages(conversationId, 200, 0, true)
      .then((data) => {
        if (!cancelled) {
          chatDebug("durable_fetch", { conversationId, messages: data, revision: expectedRevision });
          historyRef.current.onDurableMessagesLoaded(conversationId, data, expectedRevision);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoadError(
            err instanceof ApiError
              ? err.message
              : "메시지를 불러오는 중 오류가 발생했습니다."
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    chatDebug("chatwindow_props", { conversationId, messages, revision: historyRevision });
    // ChatWindow maps this array directly; there is no render-side filter, sort, slice, or dedupe.
    chatDebug("render", { conversationId, messages, revision: historyRevision });
  }, [conversationId, historyRevision, messages]);

  const handleSend = async (content: string) => {
    if (!conversationId || sendingRef.current) return;
    sendingRef.current = true;
    setSendError(null);
    chatDebug("before_send", { conversationId, messages, revision: historyRevision });

    // 낙관적으로 먼저 화면에 표시할 임시 메시지
    const tempId = `temp-${Date.now()}`;
    const optimistic: LocalMessage = {
      id: tempId,
      conversation_id: conversationId,
      role: "user",
      content,
      source_device: sourceDevice,
      sequence: null,
      metadata: {},
      timestamp: new Date().toISOString(),
      created_at: new Date().toISOString(),
      _pending: true,
    };
    const pendingSend = onSendStarted(conversationId, optimistic);

    try {
      const reply = await api.sendChatMessage({
        conversation_id: conversationId,
        role: "user",
        content,
        source_device: sourceDevice,
      });
      if (onSendSucceeded(pendingSend, reply)) onStateUpdated();
    } catch (error) {
      if (onSendFailed(pendingSend)) {
        setSendError(error instanceof ApiError ? error.message : "메시지를 전송하지 못했습니다.");
      }
    } finally {
      sendingRef.current = false;
    }
  };

  if (!conversationId) {
    return (
      <div className="chat-window chat-window-empty">
        <button className="navigation-toggle chat-navigation-toggle" type="button" onClick={onToggleSidebar}>Menu</button>
        <div className="empty-state">{loadingConversation ? `${personaDisplayName} 대화를 준비하는 중...` : `${personaDisplayName} 대화를 열지 못했습니다.`}</div>
      </div>
    );
  }

  return (
    <div className="chat-window">
      <div className="chat-header">
        <button className="navigation-toggle chat-navigation-toggle" type="button" onClick={onToggleSidebar}>Menu</button>
        <PersonaAvatar personaId={personaId} displayName={personaDisplayName} avatarExtension={personaAvatarExtension} className="header-avatar" />
        <div className="chat-header-copy"><span className="chat-header-title">{personaDisplayName}</span><span className="chat-header-subtitle">Continuous record</span></div>
        <span className="chat-header-spacer" />
      </div>

      <div className="message-list">
        {loading && <div className="empty-state">불러오는 중...</div>}

        {loadError && (
          <div className="error-banner">
            메시지를 불러오지 못했습니다: {loadError}
          </div>
        )}
        {sendError && <div className="error-banner">{sendError} <button onClick={() => setSendError(null)}>닫기</button></div>}

        {!loading && !loadError && messages.length === 0 && (
          <div className="empty-state">
            아직 메시지가 없습니다. 첫 메시지를 보내보세요.
          </div>
        )}

        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            message={m}
            personaDisplayName={personaDisplayName}
            pending={m._pending}
            failed={m._failed}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {sending && <div className="thinking-indicator">{personaDisplayName}가 생각 중...</div>}
      <MessageInput onSend={handleSend} disabled={loading} sending={sending} />
    </div>
  );
}

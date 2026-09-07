// src/components/MessageBubble.tsx

import type { MessageRead, MessageRole } from "../types/api";
import { formatKstDateTime, formatKstTime } from "../utils/datetime";
import { PersonaAvatar } from "./PersonaAvatar";

// role별 표시 이름. user/diana 외에 system/tool도 스펙에 있으므로 표시는 해준다.
function roleLabel(role: MessageRole, personaDisplayName: string): string {
  switch (role) {
    case "diana":
      return personaDisplayName;
    case "user":
      return "사용자";
    case "system":
      return "System";
    case "tool":
      return "Tool";
  }
}

interface MessageBubbleProps {
  message: MessageRead;
  personaDisplayName: string;
  personaId: string | null;
  personaAvatarExtension?: string | null;
  personaAvatarRevision?: number;
  pending?: boolean; // 전송 중(낙관적 업데이트) 표시
  failed?: boolean; // 전송 실패 표시
}

export function MessageBubble({
  message,
  personaDisplayName,
  personaId,
  personaAvatarExtension,
  personaAvatarRevision,
  pending,
  failed,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isPersonaMessage = message.role === "diana";

  return (
    <div className={`message-row ${isUser ? "message-row-user" : ""}`}>
      {isPersonaMessage && <PersonaAvatar personaId={personaId} displayName={personaDisplayName} avatarExtension={personaAvatarExtension} revision={personaAvatarRevision} className="message-avatar" />}
      {!isUser && !isPersonaMessage && <span className="message-avatar message-avatar-neutral" aria-label={roleLabel(message.role, personaDisplayName)}>●</span>}
      <div
        className={`message-bubble ${
          isUser ? "message-bubble-user" : "message-bubble-diana"
        } ${failed ? "message-bubble-failed" : ""}`}
      >
        <div className="message-role">{roleLabel(message.role, personaDisplayName)}</div>
        <div className="message-content">{message.content}</div>
        <div className="message-meta">
          {pending
            ? "전송 중..."
            : failed
            ? "전송 실패"
          : <time dateTime={message.timestamp} title={formatKstDateTime(message.timestamp)} aria-label={`${formatKstDateTime(message.timestamp)} (Asia/Seoul)`}>{formatKstTime(message.timestamp)}</time>}
        </div>
      </div>
    </div>
  );
}

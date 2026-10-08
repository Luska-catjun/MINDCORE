// src/components/MessageBubble.tsx

import type { MessageRead } from "../types/api";
import { messageRoleLabel } from "../messageLabels";
import { formatKstDateTime, formatKstTime } from "../utils/datetime";
import { PersonaAvatar } from "./PersonaAvatar";

interface MessageBubbleProps {
  message: MessageRead;
  personaDisplayName: string;
  userDisplayName: string;
  personaId: string | null;
  personaAvatarExtension?: string | null;
  personaAvatarRevision?: number;
  pending?: boolean; // 전송 중(낙관적 업데이트) 표시
  failed?: boolean; // 전송 실패 표시
}

export function MessageBubble({
  message,
  personaDisplayName,
  userDisplayName,
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
      {!isUser && !isPersonaMessage && <span className="message-avatar message-avatar-neutral" aria-label={messageRoleLabel(message.role, personaDisplayName, userDisplayName)}>●</span>}
      <div
        className={`message-bubble ${
          isUser ? "message-bubble-user" : "message-bubble-diana"
        } ${failed ? "message-bubble-failed" : ""}`}
      >
        <div className="message-role">{messageRoleLabel(message.role, personaDisplayName, userDisplayName)}</div>
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

// src/components/MessageInput.tsx

import { useState, type KeyboardEvent } from "react";

interface MessageInputProps {
  onSend: (content: string) => void;
  disabled: boolean;
  sending?: boolean;
}

export function MessageInput({ onSend, disabled, sending = false }: MessageInputProps) {
  const [value, setValue] = useState("");

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled || sending) return;
    onSend(trimmed);
    setValue("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter로 전송, Shift+Enter는 줄바꿈
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="message-input-bar">
      <textarea
        className="message-input"
        placeholder="메시지를 입력하세요..."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={1}
        disabled={disabled || sending}
      />
      <button
        className="send-btn"
        onClick={handleSend}
        disabled={disabled || sending || !value.trim()}
      >
        {sending ? "전송 중..." : "전송"}
      </button>
    </div>
  );
}

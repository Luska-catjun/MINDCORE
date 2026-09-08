import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export const MAX_AVATAR_BYTES = 2 * 1024 * 1024;

type AvatarPayload = { mime_type: string; bytes: number[] };

export async function avatarFileBytes(file: File): Promise<number[]> {
  if (file.size === 0 || file.size > MAX_AVATAR_BYTES) {
    throw new Error("Choose a PNG, JPEG, or WebP image under 2 MB.");
  }
  return Array.from(new Uint8Array(await file.arrayBuffer()));
}

export function PersonaAvatar({
  personaId,
  displayName,
  avatarExtension,
  revision = 0,
  className,
}: {
  personaId: string | null;
  displayName: string;
  avatarExtension?: string | null;
  /** Bumped when the managed avatar file is replaced without changing its extension. */
  revision?: number;
  className?: string;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const avatarClassName = ["persona-avatar", className].filter(Boolean).join(" ");

  useEffect(() => {
    let disposed = false;
    let objectUrl: string | null = null;
    setSrc(null);
    if (!personaId || !avatarExtension) return () => undefined;
    void invoke<AvatarPayload | null>("read_persona_avatar", { personaId }).then((avatar) => {
      if (disposed || !avatar) return;
      objectUrl = URL.createObjectURL(new Blob([new Uint8Array(avatar.bytes)], { type: avatar.mime_type }));
      objectUrlRef.current = objectUrl;
      setSrc(objectUrl);
    }).catch(() => {
      if (!disposed) setSrc(null);
    });
    return () => {
      disposed = true;
      if (objectUrl && objectUrlRef.current === objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrlRef.current = null;
      }
    };
  }, [avatarExtension, personaId, revision]);

  const useFallback = () => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    setSrc(null);
  };

  if (src) return <img className={avatarClassName} src={src} alt={`${displayName} avatar`} onError={useFallback} />;
  return <span className={`${avatarClassName} persona-avatar-fallback`} aria-label={`${displayName} avatar`}>{displayName.trim().slice(0, 1).toUpperCase() || "?"}</span>;
}

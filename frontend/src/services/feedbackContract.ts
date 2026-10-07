/** Public endpoint only: query/fragment/userinfo never carry credentials. */
export function validFeedbackEndpoint(value: string): boolean {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    return value === value.trim() && !/[\s?#]/.test(value) && /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,63}$/.test(host) && url.protocol === "https:" && !!host && !url.username && !url.password && !url.search && !url.hash
      && !host.includes(":") && !/^\d+(\.\d+){3}$/.test(host) && host !== "localhost"
      && !/\.(localhost|local|internal|test|invalid|example)$/.test(host);
  } catch { return false; }
}
export const CATEGORY_LABELS = { bug: "버그", feature: "기능 제안", usability: "사용성/UI", other: "기타" } as const;
export const DIAGNOSTIC_RULES: Record<string, RegExp> = {
  osVersion: /^\d{1,3}(?:\.\d{1,3}){0,3}$/,
  architecture: /^(arm64|aarch64|arm64-v8a|x86_64|x64|x86)$/,
  provider: /^(gemini|groq|anthropic|xai|openai)$/,
  backendStatus: /^(connected|disconnected|loading|error)$/,
  personaConnection: /^(BOUND_MATCH|UNBOUND|BOUND_MISMATCH|DB_UNAVAILABLE)$/,
  updaterState: /^(idle|checking|up-to-date|available|downloading|ready|installing|error|IDLE|CHECKING|UP_TO_DATE|AVAILABLE|DOWNLOADING|READY|INSTALLING|ERROR)$/,
};

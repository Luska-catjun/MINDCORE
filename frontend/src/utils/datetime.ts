const SEOUL_TIME_ZONE = "Asia/Seoul";

type TimestampValue = string | null | undefined;

/**
 * Parse API timestamps as UTC. Turso can return legacy TEXT values without an
 * offset ("YYYY-MM-DD HH:mm:ss"); those values were written as UTC, so the
 * browser must not interpret them in its own local timezone.
 */
export function parseUtcTimestamp(value: TimestampValue): Date | null {
  if (!value) return null;

  const trimmed = value.trim();
  if (!trimmed) return null;

  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed);
  const normalized = hasOffset
    ? trimmed
    : /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(trimmed)
      ? `${trimmed.replace(" ", "T")}Z`
      : trimmed;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function parts(value: TimestampValue, includeSeconds: boolean): Record<string, string> | null {
  const date = parseUtcTimestamp(value);
  if (!date) return null;

  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: SEOUL_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {}),
    hourCycle: "h23",
  });
  return Object.fromEntries(formatter.formatToParts(date).map(({ type, value: part }) => [type, part]));
}

/** Formats a canonical UTC timestamp for the user-facing Korean UI. */
export function formatKstDateTime(value: TimestampValue, includeSeconds = false): string {
  const formatted = parts(value, includeSeconds);
  if (!formatted) return "–";
  const time = includeSeconds
    ? `${formatted.hour}:${formatted.minute}:${formatted.second} KST`
    : `${formatted.hour}:${formatted.minute}`;
  return `${formatted.year}-${formatted.month}-${formatted.day} ${time}`;
}

/** Compact chat timestamp; its title/aria label should expose the full KST date. */
export function formatKstTime(value: TimestampValue): string {
  const formatted = parts(value, false);
  return formatted ? `${formatted.hour}:${formatted.minute}` : "–";
}

export function formatKstDateTimeWithSeconds(value: TimestampValue): string {
  return formatKstDateTime(value, true);
}

export { SEOUL_TIME_ZONE };

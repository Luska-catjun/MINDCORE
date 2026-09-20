export const STARTUP_DIAGNOSTIC_PREFIX = "MINDCORE_STARTUP_DIAGNOSTIC";
export const DIAGNOSTIC_BUILD_LABEL = "v0.2.2-diagnostic-1";

const allowedPhases = new Set([
  "settings", "identity", "persona_registry", "database_connect",
  "schema_classification", "schema_authority", "migration_ledger",
  "narrative_hydration", "self_model_hydration", "sidecar_spawn", "ready",
]);
const allowedCategories = new Set([
  "configuration", "connection", "native_or_network", "driver", "schema",
  "hydration", "timeout", "process", "runtime",
]);
const allowedOperations = new Set([
  "ledger_check", "ledger_create", "ledger_baseline_insert", "ledger_validate",
  "ledger_commit", "sidecar_spawn", "sidecar_exit", "ready_wait",
]);
const safeIdentifier = /^[A-Za-z0-9_.-]+$/;

export type StartupDiagnostic = {
  line: string;
  phase: string;
  category: string;
};

export function parseStartupDiagnostic(error: unknown): StartupDiagnostic | null {
  const text = typeof error === "string"
    ? error
    : error instanceof Error
      ? error.message
      : "";
  const start = text.indexOf(`${STARTUP_DIAGNOSTIC_PREFIX} `);
  if (start < 0) return null;
  const source = text.slice(start).split(/\r?\n/, 1)[0];
  const fields = new Map<string, string>();
  for (const item of source.slice(STARTUP_DIAGNOSTIC_PREFIX.length + 1).split(/\s+/)) {
    const separator = item.indexOf("=");
    if (separator < 1) continue;
    const key = item.slice(0, separator);
    const value = item.slice(separator + 1);
    if (["build", "phase", "category", "operation", "exception_class", "schema_version"].includes(key)) {
      fields.set(key, value);
    }
  }
  const phase = fields.get("phase") ?? "";
  const category = fields.get("category") ?? "";
  const exceptionClass = fields.get("exception_class") ?? "";
  if (
    fields.get("build") !== DIAGNOSTIC_BUILD_LABEL
    || !allowedPhases.has(phase)
    || !allowedCategories.has(category)
    || !safeIdentifier.test(exceptionClass)
    || exceptionClass.length > 96
  ) return null;

  const parts = [
    STARTUP_DIAGNOSTIC_PREFIX,
    `build=${DIAGNOSTIC_BUILD_LABEL}`,
    `phase=${phase}`,
    `category=${category}`,
  ];
  const operation = fields.get("operation");
  if (operation && allowedOperations.has(operation)) parts.push(`operation=${operation}`);
  parts.push(`exception_class=${exceptionClass}`);
  const schemaVersion = fields.get("schema_version");
  if (schemaVersion && /^\d{1,4}$/.test(schemaVersion)) parts.push(`schema_version=${schemaVersion}`);
  return { line: parts.join(" "), phase, category };
}

export function readyTimeoutDiagnostic(): StartupDiagnostic {
  return parseStartupDiagnostic(
    `${STARTUP_DIAGNOSTIC_PREFIX} build=${DIAGNOSTIC_BUILD_LABEL} phase=ready category=timeout operation=ready_wait exception_class=HealthTimeout`,
  )!;
}

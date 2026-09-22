export const STARTUP_DIAGNOSTIC_PREFIX = "MINDCORE_STARTUP_DIAGNOSTIC";
export const DIAGNOSTIC_BUILD_LABEL = "v0.2.2-diagnostic-2";

const allowedPhases = new Set([
  "settings", "identity", "persona_registry", "database_connect",
  "schema_classification", "schema_authority", "migration_ledger",
  "narrative_hydration", "self_model_hydration", "sidecar_spawn", "ready",
]);
const allowedProgressPhases = new Set([
  "settings", "identity", "database_connect", "schema_classification",
  "schema_authority", "migration_ledger", "narrative_hydration",
  "self_model_hydration", "ready",
]);
const allowedCategories = new Set([
  "configuration", "connection", "native_or_network", "driver", "schema",
  "hydration", "timeout", "process", "runtime",
]);
const allowedOperations = new Set([
  "settings_load", "identity_load", "pool_create", "pool_acquire",
  "schema_classify_initial", "schema_classify_final", "schema_ensure",
  "ledger_check", "ledger_create", "ledger_baseline_insert", "ledger_validate",
  "ledger_commit", "narrative_hydrate", "self_model_hydrate", "lifespan_ready",
  "sidecar_spawn", "sidecar_exit", "ready_wait",
]);
const allowedProgressOperations = new Set([
  "settings_load", "identity_load", "pool_create", "pool_acquire",
  "schema_classify_initial", "schema_classify_final", "schema_ensure",
  "ledger_check", "ledger_create", "ledger_baseline_insert", "ledger_validate",
  "ledger_commit", "narrative_hydrate", "self_model_hydrate", "lifespan_ready",
  "ready_wait",
]);
const safeIdentifier = /^[A-Za-z0-9_.-]+$/;

export type StartupDiagnostic = {
  line: string;
  phase: string;
  category: string;
  operation?: string;
  exceptionClass: string;
  lastPhase?: string;
  lastOperation?: string;
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
    if (["build", "phase", "category", "operation", "exception_class", "schema_version", "last_phase", "last_operation"].includes(key)) {
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
  const lastPhase = fields.get("last_phase");
  if (lastPhase && allowedProgressPhases.has(lastPhase)) parts.push(`last_phase=${lastPhase}`);
  const lastOperation = fields.get("last_operation");
  if (lastOperation && allowedProgressOperations.has(lastOperation)) parts.push(`last_operation=${lastOperation}`);
  return {
    line: parts.join(" "),
    phase,
    category,
    operation: operation && allowedOperations.has(operation) ? operation : undefined,
    exceptionClass,
    lastPhase: lastPhase && allowedProgressPhases.has(lastPhase) ? lastPhase : undefined,
    lastOperation: lastOperation && allowedProgressOperations.has(lastOperation) ? lastOperation : undefined,
  };
}

export function readyTimeoutDiagnostic(): StartupDiagnostic {
  return parseStartupDiagnostic(
    `${STARTUP_DIAGNOSTIC_PREFIX} build=${DIAGNOSTIC_BUILD_LABEL} phase=ready category=timeout operation=ready_wait exception_class=HealthTimeout`,
  )!;
}

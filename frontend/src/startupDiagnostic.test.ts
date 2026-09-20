import { describe, expect, it } from "vitest";
import {
  DIAGNOSTIC_BUILD_LABEL,
  parseStartupDiagnostic,
  readyTimeoutDiagnostic,
} from "./startupDiagnostic";

describe("startup diagnostic parser", () => {
  it("retains only allowlisted diagnostic fields", () => {
    const parsed = parseStartupDiagnostic(
      `invoke failed: MINDCORE_STARTUP_DIAGNOSTIC build=${DIAGNOSTIC_BUILD_LABEL} phase=migration_ledger category=driver operation=ledger_commit exception_class=DatabaseError schema_version=21 secret=do-not-copy`,
    );
    expect(parsed?.line).toBe(
      `MINDCORE_STARTUP_DIAGNOSTIC build=${DIAGNOSTIC_BUILD_LABEL} phase=migration_ledger category=driver operation=ledger_commit exception_class=DatabaseError schema_version=21`,
    );
    expect(parsed?.line).not.toContain("secret");
  });

  it("rejects unrecognized builds and unsafe values", () => {
    expect(parseStartupDiagnostic("MINDCORE_STARTUP_DIAGNOSTIC build=other phase=ready category=timeout exception_class=Timeout")).toBeNull();
    expect(parseStartupDiagnostic(`MINDCORE_STARTUP_DIAGNOSTIC build=${DIAGNOSTIC_BUILD_LABEL} phase=ready category=timeout exception_class=bad/value`)).toBeNull();
  });

  it("provides a fixed secret-free health timeout code", () => {
    expect(readyTimeoutDiagnostic().line).toContain("operation=ready_wait");
    expect(readyTimeoutDiagnostic().line).toContain("exception_class=HealthTimeout");
  });
});

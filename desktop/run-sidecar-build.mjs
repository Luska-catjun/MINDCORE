import { existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const windows = process.platform === "win32";
const candidates = windows
  ? [join(root, ".venv", "Scripts", "python.exe"), "python"]
  : [join(root, ".venv", "bin", "python"), "python3", "python"];
// Do not infer a path from its separator: Windows absolute paths use `\\`,
// so checking for `/` selected a missing local virtualenv interpreter on CI.
// Prefer a real project virtualenv when present, then let PATH resolve Python.
const python = candidates.find(existsSync) ?? candidates.at(-1);
const result = spawnSync(python, [join(root, "desktop", "build_sidecar.py")], {
  cwd: root,
  stdio: "inherit",
});
process.exit(result.status ?? 1);

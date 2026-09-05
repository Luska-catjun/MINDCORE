import { existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const windows = process.platform === "win32";
const candidates = windows
  ? [join(root, ".venv", "Scripts", "python.exe"), "python"]
  : [join(root, ".venv", "bin", "python"), "python3", "python"];
const python = candidates.find((candidate) => !candidate.includes("/") || existsSync(candidate));
const result = spawnSync(python, [join(root, "desktop", "build_sidecar.py")], {
  cwd: root,
  stdio: "inherit",
});
process.exit(result.status ?? 1);

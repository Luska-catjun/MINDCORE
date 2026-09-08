import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const script = join(root, "desktop", "write-updater-config.mjs");

async function generateConfig(environment) {
  const directory = await mkdtemp(join(tmpdir(), "mindcore-updater-config-"));
  const output = join(directory, "tauri.updater.conf.json");
  try {
    // CI runs its whole job with the updater disabled. Each fixture must
    // choose its own updater mode instead of inheriting that job setting.
    const childEnv = { ...process.env };
    delete childEnv.MINDCORE_UPDATER_DISABLED;
    const result = spawnSync(process.execPath, [script], {
      cwd: root,
      encoding: "utf8",
      env: { ...childEnv, ...environment, MINDCORE_UPDATER_CONFIG_PATH: output },
    });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(await readFile(output, "utf8"));
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("updater-disabled config omits the updater plugin and artifacts", async () => {
  const config = await generateConfig({ MINDCORE_UPDATER_DISABLED: "1" });
  assert.equal(config.plugins, undefined);
  assert.equal(config.bundle.createUpdaterArtifacts, false);
});

test("updater-enabled config retains the configured updater contract", async () => {
  const config = await generateConfig({
    MINDCORE_UPDATE_ENDPOINT: "https://updates.example.test/latest.json",
    MINDCORE_UPDATER_PUBKEY: "test-public-key",
  });
  assert.deepEqual(config.plugins, {
    updater: {
      pubkey: "test-public-key",
      endpoints: ["https://updates.example.test/latest.json"],
    },
  });
  assert.equal(config.bundle.createUpdaterArtifacts, true);
});

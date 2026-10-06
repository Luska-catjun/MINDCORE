import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadReleaseNotes } from "./prepare-release-notes.mjs";
test("release notes authoring requires the current version file and heading", async () => {
  const root = await mkdtemp(join(tmpdir(), "mindcore-notes-"));
  try {
    await mkdir(join(root, "frontend")); await mkdir(join(root, "release-notes"));
    await writeFile(join(root, "frontend/package.json"), JSON.stringify({version: "0.4.0"}));
    await assert.rejects(loadReleaseNotes(root), /Missing release notes/);
    await writeFile(join(root, "release-notes/0.4.0.md"), "# MindCore 0.3.0\n");
    await assert.rejects(loadReleaseNotes(root), /mismatch/);
    await writeFile(join(root, "release-notes/0.4.0.md"), "# MindCore 0.4.0\n\n[공지](https://example.com)");
    assert.match((await loadReleaseNotes(root)).markdown, /https:/);
  } finally { await rm(root, { recursive: true }); }
});

import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
export async function loadReleaseNotes(root) {
  const metadata = JSON.parse(await readFile(resolve(root, "frontend/package.json"), "utf8"));
  const version = metadata.version;
  const path = resolve(root, "release-notes", `${version}.md`);
  let markdown;
  try { markdown = await readFile(path, "utf8"); } catch { throw new Error(`Missing release notes for ${version}: ${path}`); }
  if (!markdown.startsWith(`# MindCore ${version}\n`) || Buffer.byteLength(markdown) > 65536) throw new Error(`Release notes version/size mismatch for ${version}`);
  return { version, markdown };
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const notes = await loadReleaseNotes(resolve(import.meta.dirname, ".."));
  const output = resolve(import.meta.dirname, "../frontend/src/generated");
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, "release-notes.json"), JSON.stringify(notes));
  console.log(`RELEASE_NOTES_OK=${notes.version}`);
}

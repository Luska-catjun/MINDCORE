import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const desktopRoot = resolve(import.meta.dirname, "../frontend/src-tauri");
const output = process.env.MINDCORE_UPDATER_CONFIG_PATH
  ? resolve(process.env.MINDCORE_UPDATER_CONFIG_PATH)
  : resolve(desktopRoot, "tauri.updater.conf.json");

// This public development key verifies only local/mock releases. Production
// builds must override it through CI with MINDCORE_UPDATER_PUBKEY; the private
// signing key is never read by this script or bundled with the application.
const developmentPublicKey = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDY0MjJGRjYxMTQ2MDM4QTkKUldTcE9HQVVZZjhpWk0zc25UUjZ5aTczV09tSnJHSTI3NEJMNVBsVU16Smthem5zZDF2VTNrZDQK";
const updaterDisabled = process.env.MINDCORE_UPDATER_DISABLED === "1";
const endpoint = process.env.MINDCORE_UPDATE_ENDPOINT
  ?? "https://updates.mindcore.invalid/{{target}}/{{arch}}/{{current_version}}";
const pubkey = process.env.MINDCORE_UPDATER_PUBKEY ?? developmentPublicKey;

if (!updaterDisabled && !endpoint.startsWith("https://")) {
  throw new Error("MINDCORE_UPDATE_ENDPOINT must use HTTPS.");
}
if (!updaterDisabled && !pubkey.trim()) {
  throw new Error("MINDCORE_UPDATER_PUBKEY is required for an updater build.");
}
const cargoToml = await readFile(resolve(desktopRoot, "Cargo.toml"), "utf8");
const version = cargoToml.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (!version) {
  throw new Error("Could not read the authoritative package version from Cargo.toml.");
}

const config = {
  version,
  ...(updaterDisabled ? {} : { plugins: { updater: { pubkey, endpoints: [endpoint] } } }),
  bundle: { createUpdaterArtifacts: !updaterDisabled },
};
await writeFile(output, `${JSON.stringify(config, null, 2)}\n`, "utf8");
console.log(updaterDisabled ? "MindCore updater disabled for unsigned CI build" : `MindCore updater configured for ${new URL(endpoint).origin}`);

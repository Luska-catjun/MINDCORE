# MindCore updater release boundary

MindCore uses Tauri's official updater plugin. The client only receives a
public verification key and an HTTPS manifest endpoint. It never receives a
GitHub token, a signing private key, or any Turso/LLM credential.

## Build configuration

`npm run tauri:build` writes an ignored `tauri.updater.conf.json`. The version
is read from `frontend/src-tauri/Cargo.toml`. For a release, CI must provide:

```text
MINDCORE_UPDATE_ENDPOINT=https://public-updates.example.com/{{target}}/{{arch}}/{{current_version}}
MINDCORE_UPDATER_PUBKEY=<production public minisign key>
TAURI_SIGNING_PRIVATE_KEY_PATH=<CI secret file>
TAURI_SIGNING_PRIVATE_KEY_PASSWORD=<CI secret>
```

The default endpoint is deliberately unreachable and the default public key is
only for local/mock acceptance builds. Production signing material must never
be committed or passed through Vite variables.

## Manifest

The public endpoint serves the official Tauri updater JSON for the requesting
platform, containing the semantic version, optional plain-text release notes,
and platform artifact URL plus signature. The Tauri plugin performs semantic
version ordering and signature verification before install; a SHA256 can be
published as extra release metadata but is not the trust decision.

The application stores its config, identity, and Turso credentials in its app
config directory, outside the application bundle. An update replaces only the
signed application artifact.

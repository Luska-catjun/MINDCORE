# MindCore frontend

The React client serves both the web observation surface and the Tauri desktop
shell. The desktop package starts a local loopback FastAPI sidecar; web builds
use the configured API base URL.

```bash
npm ci
npm run dev
npm test -- --run
npm run lint
npm run build
```

For desktop packaging, use `npm run tauri:build` on macOS or
`npm run tauri:build:windows` on a native Windows x64 host. The first-run
wizard creates local configuration and a generic Persona identity; no provider
or database credentials belong in Vite environment variables or source.

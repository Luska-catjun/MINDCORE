# MindCore Desktop — Windows developer build

This is a native Windows x64 build path for `x86_64-pc-windows-msvc`. It does
not cross-compile from macOS, publish a release, or add automatic release
automation.

## Prerequisites

Developer machines need Python **3.12**, Node.js (current LTS), Rust stable
with the **MSVC** toolchain, and Visual Studio Build Tools with the Desktop C++
workload. Tauri uses WebView2 on Windows; the standard Tauri/NSIS installer
handles its normal runtime behavior. End users of the finished installer do
not need Python, Rust, Node, or a virtual environment.

## Build

Open PowerShell on a Windows x64 machine and run these commands from a fresh
checkout:

```powershell
git pull
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r desktop\requirements.txt

cd frontend
npm ci
```

`npm run tauri:build:windows` invokes the same host-native PyInstaller helper
as macOS. On Windows it produces
`src-tauri\binaries\mindcore-backend-x86_64-pc-windows-msvc.exe`, which is the
Tauri `externalBin` naming convention for the sidecar, then asks Tauri to build
an NSIS installer.

For a signed updater-capable development/release build, set only CI or local
shell environment values (never commit them):

```powershell
$env:MINDCORE_UPDATE_ENDPOINT = "https://updates.example.com/{{target}}/{{arch}}/{{current_version}}"
$env:MINDCORE_UPDATER_PUBKEY = "<public-minisign-key>"
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content "C:\secure\mindcore.key" -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "<signing-key-password>"
npm run tauri:build:windows
```

Without those overrides, the build uses the repository's safe local/mock
public verification key and an `.invalid` HTTPS endpoint; it is not a public
release configuration. The signing private key, provider keys, Turso token,
and user identity remain outside the source tree and application bundle.

The expected native build output directory is:

```text
frontend\src-tauri\target\release\bundle\nsis\
```

Tauri determines the final NSIS filename. With updater artifacts enabled, its
normal Windows NSIS installer and corresponding signature are produced in that
directory. Actual Windows build, install, first-run setup, chat, shutdown, and
update A→B testing remain required before a Windows release.

# MindCore

MindCore is a persistent cognition and persona runtime for AI applications.
It provides a configurable local desktop shell, a durable state model, and
observation tools without shipping a fixed character or private user data.

MindCore is currently alpha software. Its public default persona is the
generic [`identity_template.txt`](app/prompts/identity_template.txt); an owner
configures a persona, database connection, and model provider during first-run
setup. Credentials and persona files remain local to the installed app.

The repository includes a Tauri desktop shell and a bundled local FastAPI
sidecar. See [desktop/WINDOWS.md](desktop/WINDOWS.md) for the native Windows
x64 developer build path. Release automation is available through GitHub
Actions; production signing material is intentionally not stored in this repo.

License decision pending.

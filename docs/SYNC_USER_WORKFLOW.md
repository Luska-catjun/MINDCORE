# Desktop manual secure sync workflow candidate

Select a Persona and open **Sync / Devices**. The panel shows its scope, this
device's fingerprint, trusted peers, manual status, and pending conflicts.
The supported Desktop scope is
`MANUAL_SECURE_LOCAL_FILE_DESKTOP_PERSONAS`: select a local `file:`
schema-24 Persona database. A remote `libsql:`/Turso Persona shows a clear
unavailable message and has no Sync actions. Direct Tauri calls for that
Persona reject before sidecar invocation, pairing state, checkpoint, or
domain mutation. This version does not copy or download a remote Persona.

Opening the panel scans the selected local Persona to establish a baseline
before new offline edits. The initial baseline is not an outbound change;
both devices should begin from the same Persona package.

1. Export each device's `.mindcorepair` file. Import the other file, compare
   the full fingerprint by a separate trusted channel, and enter it before
   trusting the peer. Give the peer a recognizable display name.
2. Choose the trusted recipient and export a `.mindcoresync` update through
   the native file dialog. Transfer the file manually and import it through
   the same dialog on the peer. Repeat after independent offline edits.
3. Open Conflict Inbox to compare versions. Keep either mutable version, or
   choose the updated item or a confirmed shared tombstone in a delete/update
   race. Export the resulting resolution and import it on the peer. Repeat
   until both inboxes have no pending normal conflict and a fresh round
   applies zero rows.
4. An immutable ID payload mismatch is a repair case. The UI shows hash
   fingerprints and does not offer an ordinary choose-one action.
5. Confirm **Revoke** to stop trusting a peer. Later encrypted files from it
   are rejected locally. Device trust is global; status and conflicts are
   isolated by selected Persona.

The UI displays safe messages for unknown or revoked peers, wrong Persona,
invalid/tampered files, replay, unsupported versions, and unavailable crypto.
Manual sync failure does not prevent local Persona chat.

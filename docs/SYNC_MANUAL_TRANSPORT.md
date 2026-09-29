# Manual Secure Sync Transport — Candidate Contract

This document describes the M7.3.1 Desktop CLI and its M7.3.1.1 accepted
Android/Desktop protocol-v1 interoperability. It remains a manual file
transport, not a production sync UI or live Turso workflow. The protocol
authority remains Android's `docs/SYNC_PROTOCOL_V1.md` and
`docs/SYNC_SECURITY_MODEL.md`.

## Artifacts

- `.mindcorepair` contains a protocol version, device ID, public P-256 key,
  and SHA-256 fingerprint. It contains no private key or Persona records.
- `.mindcoresync` contains the authenticated protocol-v1 envelope fields only.
  Persona delta plaintext is encrypted with AES-256-GCM before the artifact is
  written. The file transport copies bytes; it does not inspect or merge
  Persona data.

## Desktop CLI candidate

Use the repository virtual environment and an app-private state directory. The
database argument must be a local schema-24 SQLite snapshot explicitly chosen
by the operator. This CLI does not connect to live Turso and is not wired into
the production Desktop UI.

```text
python scripts/mindcore_sync.py identity --state-dir <private-state-dir>
python scripts/mindcore_sync.py pair-export --state-dir <private-state-dir> --out <new-file.mindcorepair>
python scripts/mindcore_sync.py pair-import --state-dir <private-state-dir> --artifact <peer.mindcorepair> --confirm-fingerprint <manually-verified-fingerprint>
python scripts/mindcore_sync.py export --state-dir <private-state-dir> --database <local-schema24.sqlite> --persona-id <persona-uuid> --peer <peer-device-uuid> --out <new-file.mindcoresync>
python scripts/mindcore_sync.py apply --state-dir <private-state-dir> --database <local-schema24.sqlite> --persona-id <persona-uuid> --artifact <received.mindcoresync>
```

Import/export artifacts must be moved out of band. Pairing requires comparing
the displayed fingerprint and explicitly supplying the confirmed value. A
device ID alone never establishes trust. Revocation is local to that device
until M7.3.2 defines the user-facing lifecycle.

## File handling and security properties

- Pair and sync files use strict field allowlists, byte limits, regular-file
  checks, symlink rejection, path traversal rejection, `O_NOFOLLOW` where
  available, exclusive create, mode `0600`, file fsync, and no overwrite.
- Long-term private P-256 identity material is stored in an accepted native OS
  credential backend; it is not written to Persona SQLite or a plaintext key
  file. The Desktop sidecar stores trust and replay metadata separately.
- Envelope AAD binds protocol version, sender, recipient, Persona, message ID,
  sequence, and message type. HKDF and nonce derivation follow the Android
  protocol-v1 construction.
- Manual file transfer does not authenticate the person physically handling
  the file. The operator must compare pairing fingerprints through a separate
  trusted channel. Anyone who obtains an encrypted sync artifact can observe
  its routing metadata and size even though Persona delta content is encrypted.
- Import applies a sync delta, not a user turn. It must not invoke a provider.

## Failure modes

Unknown/revoked peer, fingerprint mismatch, wrong Persona/recipient, malformed
or oversized artifact, tampering, stale sequence, replay, corrupt trust sidecar,
unavailable keyring, path collision, and schema drift fail closed with safe
error codes. A pending inbound replay reservation can be retried after a crash;
the applied marker is written after domain commit/checkpoint processing.

## M7.3.1.1 acceptance

The procedural closure report records actual AndroidKeyStore and Desktop
native-keyring pairing, mutual fingerprint confirmation, trust persistence
across Android process restart, and revoked-peer rejection in both directions.
Actual Android secure envelopes were applied by the Desktop CLI and actual
Desktop envelopes were applied by the Android secure inbound path. The same
acceptance exercised replay, tamper, wrong peer and Persona rejection, three
offline append rounds, mutable convergence and durable conflicts, explicit
tombstones, delete/update conflicts, and two-Persona isolation. Historical
M7.3.1 remains `FAIL / NO-GO`; its procedure and lack of E2E evidence are not
rewritten by this result.

These results accept the Desktop counterpart and manual secure transport as
foundations. They do not enable `PERSONA_SYNC`: conflict resolution and the
production trusted-device, export, import, and status UI remain for M7.3.2.
See `M7_3_1_1_DESKTOP_SYNC_CLOSURE_REPORT.md` for exact test runs, artifact
sizes, security checks, and the final marker.

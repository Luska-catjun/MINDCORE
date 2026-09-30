# Desktop manual sync conflict resolution candidate

The Desktop implementation shares protocol-v1 resolution fields and canonical
hashing with Android. A `resolutions` operation travels inside the existing
AES-GCM authenticated `.mindcoresync` envelope. It names the Persona, mutable
entity, two conflicting content hashes, selected result or tombstone, and
optional IDs of two competing prior resolutions. The result revision binds
the heads. The deterministic ID allows logical deduplication.

The Conflict Inbox offers local/peer versions for `CONCURRENT_MUTATION` and
updated/deleted outcomes for `TOMBSTONE_CONFLICT`. The receiver applies only
when the referenced heads match a durable pending conflict; stale or unrelated
operations fail closed. Simultaneous different decisions create
`RESOLUTION_CONFLICT` and require a second explicit choice. Repeating a
resolution causes zero additional domain mutations. `IDENTITY_PAYLOAD_CONFLICT`
is shown as an integrity repair case without an overwrite action.

The domain commit precedes the local sidecar checkpoint, allowing retry after
process death. Sidecar schema 5 retains hashes, operation IDs, conflict
metadata, and authenticated pending ciphertext, not raw Persona content.
The retained hash of a row behind a semantic tombstone distinguishes a stable
physical row from true ID reuse. Cognition rows and their descendants are not
cascade-deleted. Neither sync apply nor resolution invokes a provider or
post-cognition work. Persona schema remains 24.

The production React panel invokes Tauri's `manual_sync_action`, which calls
the bundled Python sidecar for a selected local schema-24 Persona database.
See [SYNC_USER_WORKFLOW.md](SYNC_USER_WORKFLOW.md).

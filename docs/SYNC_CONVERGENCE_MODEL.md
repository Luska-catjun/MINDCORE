# Persona Sync Convergence Model — Candidate Contract

## M7.3.2 resolution candidate

The optional protocol-v1 `resolutions` delta field carries encrypted,
authenticated first-class operations. A result supersedes two content-hash
heads; different simultaneous results become a new explicit conflict, which
can itself be resolved with `supersedes` references. Replay is logically
idempotent. Local domain commits precede sidecar checkpoints so a retry after
process death does not repeat the domain mutation. Semantic tombstones retain
physical cognition rows and sidecar schema 5 records the retained row hash.
Persona schema stays 24. See [SYNC_CONFLICT_RESOLUTION.md](SYNC_CONFLICT_RESOLUTION.md).

This describes protocol-v1 behavior accepted through the M7.3.1.1 synthetic
Android/Desktop convergence run. It establishes a local-file foundation only;
production Persona Sync remains disabled until conflict resolution and
user-facing sync flows receive their own acceptance.

## Identity and scope

Persona identity is stable across devices. `device_id` and the P-256 sync
identity are installation-local. Cognition remains in each schema-24 Persona
database. Sync metadata, peer trust, replay reservations, checkpoints, and
conflicts live in device-local sidecars. Credentials, provider/model settings,
window state, in-flight turns, retry state, and sidecars are not sync entities.

## Record rules

- A record's canonical JSON and SHA-256 hash are deterministic.
- Append-only records with new IDs can converge in either order. An identical
  already-present record is idempotent; a different payload for the same ID is
  a conflict.
- A mutable record carries a logical revision and common ancestor. An
  uncontested descendant may apply. Concurrent descendants from a shared
  ancestor are durably classified as conflicts; neither side silently chooses
  a timestamp-based winner.
- Tombstones are durable sync metadata. Remote apply records the tombstone but
  preserves the local domain row and independent cognition data. A concurrent
  update/delete is durably classified as `TOMBSTONE_CONFLICT` on both peers.
- Applying a delta is atomic for domain rows. Sync checkpoint/replay completion
  follows the domain transaction; retry after a process failure must be
  idempotent.

## Round behavior

For the accepted offline append test, both devices started from the same
logical baseline, made independent appends, and exchanged actual secure
deltas. Three repeated rounds ended with six unique records on both peers,
matching manifests, zero loss, and zero duplicates. A subsequent no-change
round was empty. The acceptance also covered Persona A/B isolation, explicit
tombstones in both directions, and uncontested mutable changes.

## Conflict behavior

Concurrent mutable edits and delete/update races remain unresolved records for
later user-directed conflict handling. Both peers durably stored the same
conflict type and preserved their local value. The protocol does not choose a
winning value or merge conflicts silently. M7.3.2 must add a deterministic or
manual resolution policy and a user-visible workflow before the `PERSONA_SYNC`
capability decision.

## Current evidence boundary

The M7.3.1.1 report records actual pair artifact exchange, authenticated
envelope exchange and apply in both directions, restart-persistent trust,
revocation, replay and tamper rejection, wrong peer/Persona rejection,
three-round offline convergence, mutable changes, conflicts, tombstones, and
two-Persona isolation. Android process-death recovery and cognition regression
reuse the accepted M7.2.1.1 baseline. Historical M7.3.1 remains FAIL because
that run lacked the required E2E and broke procedure; this separate report
does not alter its result. The current capability decision is documented in
`M7_3_1_1_DESKTOP_SYNC_CLOSURE_REPORT.md`.

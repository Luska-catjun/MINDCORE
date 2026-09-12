# Turn durability

MindCore records a chat turn before calling the provider and records every
post-response cognition stage after the assistant message is durable. The
message tables remain the authority for message content; the lifecycle tables
contain identifiers, timestamps, status, and safe error categories only.

## Lifecycle

- `pending`: the user message is durable; no durable assistant response exists.
- `core_failed`: the provider or assistant persistence failed. Recovery never
  calls the provider for this turn.
- `core_completed`: the assistant message and pending post-stage ledger are
  durable, but some stages have not completed.
- `partial`: one or more post stages failed or reached their retry limit.
- `complete`: every post stage is durably completed, including explicit no-op
  stages whose detector produced no work.

The user message and pending turn are one transaction. The provider request is
outside all database transactions. After a successful provider response, the
assistant message, `assistant_message_id`, core-completion mark, and initial
stage rows are committed together.

## Stage recovery rules

Each `(turn_id, stage_name)` is unique. A database-only stage is claimed, its
side effect runs, and its `completed` mark is written on the same leased
connection and in the same outer transaction. A failure rolls back both the
side effect and claim, then records a safe failure category separately.

Automatic recovery is limited to deterministic stages that can reconstruct
their inputs from the durable user/assistant messages and prerequisite rows.
Stages that depend on an external/model call or ephemeral same-turn state are
tracked as `manual` and are never automatically replayed. Completed stages are
always skipped. Automatic stages stop after three failed attempts. Startup
recovery processes at most ten turns in a background task and never blocks app
startup or retries a provider request.

If message deletion removes a recovery input, message foreign keys become
`NULL` and remaining automatic work is terminalized as
`missing_recovery_input`; MindCore does not infer cognition from the wrong
message. Because each Persona owns a separate pool/database, recovery cannot
cross Persona data boundaries.

## Adding a stage safely

1. Add one stable stage name and choose `automatic` only when all inputs can be
   reconstructed from durable state without a provider or other external side
   effect.
2. Run database side effects through the connection-bound pool supplied to the
   stage callback. This keeps the side effect and ledger completion in one
   transaction.
3. If atomic co-commit is impossible, require a stable database uniqueness key
   before considering automatic recovery. Otherwise mark the stage `manual`.
4. Record a completed no-op when the stage legitimately has no work; do not
   leave it pending.
5. Add failure, rollback, duplicate resume, file-backed restart, and retry-limit
   tests.

Schema version 22 introduced `chat_turns` and `chat_turn_stages` through the
forward migration `022_turn_durability`. The same definitions are present in
the fresh Turso baseline and schema contract; historical migrations are not
replayed.

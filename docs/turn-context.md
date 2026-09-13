# Turn context

`ActorContext` records who initiated a turn. It is deliberately role-only:
it never contains a display name, identity prompt, message content, provider
data, or credentials.

`TurnContext` records the initiator together with the trigger and input source.
It is stored on the authoritative `chat_turns` row as `initiator_actor`,
`trigger_type`, and `input_source`.

The only active runtime path in M1 is:

```text
user / user_message / text
```

The model can also represent the following future-only contexts, but M1 does
not schedule, create messages for, or send provider requests from them:

```text
persona / autonomy_decision / internal
system / system_event / internal
```

For compatibility with the released user-chat lifecycle, a current user turn
uses its initial user-message UUID as `turn_id`. A future persona-initiated
turn has no initial user message and will require a separate identity decision;
that transition is intentionally outside M1.

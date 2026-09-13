from dataclasses import fields
import unittest

from app.models.turn_context import (
    ActorContext,
    ActorKind,
    TurnContext,
    TurnInputSource,
    TurnTrigger,
)


class TurnContextModelTests(unittest.TestCase):
    def test_current_user_text_context_is_canonical_and_content_free(self) -> None:
        context = TurnContext.user_text()

        self.assertEqual(context.initiator.kind, ActorKind.USER)
        self.assertEqual(context.trigger, TurnTrigger.USER_MESSAGE)
        self.assertEqual(context.input_source, TurnInputSource.TEXT)
        self.assertEqual(context.durable_values(), ("user", "user_message", "text"))
        self.assertEqual([field.name for field in fields(TurnContext)], ["initiator", "trigger", "input_source"])
        with self.assertRaises(TypeError):
            TurnContext(  # type: ignore[call-arg]
                initiator=ActorContext(ActorKind.USER),
                trigger=TurnTrigger.USER_MESSAGE,
                input_source=TurnInputSource.TEXT,
                content="must not be stored",
            )

    def test_actor_trigger_and_input_enums_validate_values(self) -> None:
        self.assertEqual(ActorContext("persona").kind, ActorKind.PERSONA)
        with self.assertRaises(ValueError):
            ActorContext("unknown")
        with self.assertRaises(ValueError):
            TurnContext(ActorContext(ActorKind.USER), "unknown", TurnInputSource.TEXT)
        with self.assertRaises(ValueError):
            TurnContext(ActorContext(ActorKind.USER), TurnTrigger.USER_MESSAGE, "unknown")

    def test_only_meaningful_combinations_are_representable(self) -> None:
        self.assertEqual(
            TurnContext(
                ActorContext(ActorKind.PERSONA),
                TurnTrigger.AUTONOMY_DECISION,
                TurnInputSource.INTERNAL,
            ).durable_values(),
            ("persona", "autonomy_decision", "internal"),
        )
        self.assertEqual(
            TurnContext(
                ActorContext(ActorKind.SYSTEM),
                TurnTrigger.SYSTEM_EVENT,
                TurnInputSource.INTERNAL,
            ).durable_values(),
            ("system", "system_event", "internal"),
        )
        with self.assertRaisesRegex(ValueError, "combination"):
            TurnContext(
                ActorContext(ActorKind.PERSONA),
                TurnTrigger.USER_MESSAGE,
                TurnInputSource.TEXT,
            )


if __name__ == "__main__":
    unittest.main()

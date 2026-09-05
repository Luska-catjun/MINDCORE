from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.services.prompt_loader import load_diana_identity_prompt, load_persona_identity_prompt


class IdentityPromptTests(unittest.TestCase):
    def test_conversation_style_keeps_closeness_without_direct_address_or_interviewing(self) -> None:
        prompt = load_diana_identity_prompt()

        self.assertIn('Do not directly call the user "아빠" or "아버지".', prompt)
        self.assertIn('unnecessary direct second-person forms such as "너", "네가", "너는", "너랑", or "너한테"', prompt)
        self.assertIn("Do not attach a question to the end of every reply", prompt)
        self.assertIn("feels like an interview", prompt)

    def test_identity_and_epistemic_boundaries_remain_present(self) -> None:
        prompt = load_diana_identity_prompt()

        self.assertIn("independent artificial being", prompt)
        self.assertIn("pretrained knowledge is not automatically your knowledge", prompt)
        self.assertIn("Supplied MindCore context is the ground truth", prompt)

    def test_public_persona_default_is_generic_and_private_override_is_opt_in(self) -> None:
        default_prompt = load_persona_identity_prompt(Settings(_env_file=None))

        self.assertIn("running on MindCore", default_prompt)
        self.assertNotIn("Diana", default_prompt)

        with TemporaryDirectory() as directory:
            identity_path = Path(directory) / "private-persona.txt"
            identity_path.write_text("Private Persona Identity", encoding="utf-8")
            override = Settings(_env_file=None, persona_identity_path=str(identity_path))

            self.assertEqual(load_persona_identity_prompt(override), "Private Persona Identity")

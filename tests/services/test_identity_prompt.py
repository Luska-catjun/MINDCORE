from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.services.prompt_loader import load_persona_identity_prompt


class IdentityPromptTests(unittest.TestCase):
    def test_public_default_keeps_generic_conversation_style(self) -> None:
        prompt = load_persona_identity_prompt(Settings(_env_file=None))

        self.assertIn("Use natural, concise language", prompt)
        self.assertIn("Do not turn every response into an interview", prompt)

    def test_public_default_keeps_epistemic_boundary(self) -> None:
        prompt = load_persona_identity_prompt(Settings(_env_file=None))

        self.assertIn("pretrained information as personally acquired knowledge", prompt)
        self.assertIn("grounded context", prompt)

    def test_public_persona_default_is_generic_and_private_override_is_opt_in(self) -> None:
        default_prompt = load_persona_identity_prompt(Settings(_env_file=None))

        self.assertIn("running on MindCore", default_prompt)
        self.assertNotIn("Diana", default_prompt)

        with TemporaryDirectory() as directory:
            identity_path = Path(directory) / "private-persona.txt"
            identity_path.write_text("Private Persona Identity", encoding="utf-8")
            override = Settings(_env_file=None, persona_identity_path=str(identity_path))

            self.assertEqual(load_persona_identity_prompt(override), "Private Persona Identity")

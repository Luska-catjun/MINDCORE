from functools import lru_cache
from pathlib import Path
import sys

from app.config import Settings


class PromptLoadError(RuntimeError):
    pass


APP_DIR = Path(__file__).resolve().parents[1]
# PyInstaller exposes packaged resources through _MEIPASS.  Keeping this
# resolution here makes source, one-file sidecar, and future user-data
# overrides use the same code path.
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_DIR.parent))
PROMPTS_DIR = APP_DIR / "prompts"
DIANA_IDENTITY_PROMPT_PATH = PROMPTS_DIR / "diana_identity.txt"
GENERIC_IDENTITY_PROMPT_PATH = RESOURCE_ROOT / "app" / "prompts" / "identity_template.txt"
MEMORY_EXTRACTION_PROMPT_PATH = PROMPTS_DIR / "memory_extraction.txt"


def _load_prompt(path: Path, label: str) -> str:
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise PromptLoadError(f"{label} prompt file was not found: {path}") from exc
    except OSError as exc:
        raise PromptLoadError(f"{label} prompt file could not be read: {path}") from exc

    if not prompt:
        raise PromptLoadError(f"{label} prompt file is empty: {path}")

    return prompt


@lru_cache(maxsize=1)
def load_diana_identity_prompt() -> str:
    return _load_prompt(DIANA_IDENTITY_PROMPT_PATH, "Diana identity")


def load_persona_identity_prompt(settings: Settings) -> str:
    """Load a user-selected persona identity, or the public generic default."""
    if settings.persona_identity_path:
        return _load_prompt(Path(settings.persona_identity_path).expanduser(), "Persona identity")
    return _load_prompt(GENERIC_IDENTITY_PROMPT_PATH, "Generic persona identity")


@lru_cache(maxsize=1)
def load_memory_extraction_prompt() -> str:
    return _load_prompt(MEMORY_EXTRACTION_PROMPT_PATH, "Memory extraction")

"""Small, deterministic registry for activity-scoped procedural manuals."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


MANUAL_ROOT = Path(__file__).resolve().parents[1] / "prompts" / "skills" / "games"


@dataclass(frozen=True)
class SkillManual:
    skill_id: str
    aliases: tuple[str, ...]
    filename: str


@dataclass(frozen=True)
class SkillTurn:
    skill_id: str | None
    action: str  # activate, continue, reference, stop, none


class SkillManualRegistry:
    """Registry deliberately uses explicit aliases rather than an LLM guess."""

    _manuals = (
        SkillManual("game.updown", ("업다운", "up down"), "updown.txt"),
        SkillManual("game.twenty_questions", ("스무고개", "20고개", "twenty questions"), "twenty_questions.txt"),
        SkillManual("game.word_chain", ("끝말잇기", "word chain"), "word_chain.txt"),
        SkillManual("game.baskin_robbins_31", ("베스킨라빈스 31", "베라 31", "31게임", "baskin robbins 31"), "baskin_robbins_31.txt"),
    )

    @classmethod
    def get(cls, skill_id: str) -> SkillManual | None:
        return next((manual for manual in cls._manuals if manual.skill_id == skill_id), None)

    @classmethod
    def resolve_alias(cls, text: str) -> str | None:
        normalized = " ".join(text.casefold().split())
        matches = [
            (max(normalized.rfind(alias) for alias in manual.aliases if alias in normalized), manual.skill_id)
            for manual in cls._manuals
            if any(alias in normalized for alias in manual.aliases)
        ]
        # In an explicit switch, the last named game is the requested target.
        return max(matches, default=(-1, None))[1]


_REFERENCE_MARKERS = ("규칙", "설명", "어떻게 해", "방법", "룰")
_START_MARKERS = ("하자", "시작", "해보자", "할래", "진행", "놀이")
_STOP_MARKERS = ("그만", "끝", "다른 거 하자", "다른거 하자", "멈추")


def resolve_skill_turn(text: str, active_skill_id: str | None) -> SkillTurn:
    """Interpret only explicit known game names and stable Korean phrases."""
    normalized = " ".join(text.casefold().split())
    mentioned = SkillManualRegistry.resolve_alias(normalized)
    # A named replacement takes precedence over a stop phrase in the same turn.
    if mentioned and any(marker in normalized for marker in _START_MARKERS):
        return SkillTurn(mentioned, "activate")
    if mentioned and any(marker in normalized for marker in _REFERENCE_MARKERS):
        return SkillTurn(mentioned, "reference")
    if active_skill_id and any(marker in normalized for marker in _STOP_MARKERS):
        return SkillTurn(None, "stop")
    if active_skill_id:
        return SkillTurn(active_skill_id, "continue")
    return SkillTurn(None, "none")


@lru_cache(maxsize=8)
def load_skill_manual(skill_id: str) -> str | None:
    """Read known local files once per process; a missing manual is non-fatal."""
    manual = SkillManualRegistry.get(skill_id)
    if manual is None:
        return None
    try:
        text = (MANUAL_ROOT / manual.filename).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text[:1200] or None


def procedural_manual_context(skill_id: str | None) -> str | None:
    manual = load_skill_manual(skill_id) if skill_id else None
    if not manual:
        return None
    return "\n".join((
        "[ACTIVE SKILL MANUAL - PROCEDURAL INSTRUCTIONS]",
        "Apply this only to the active activity. It does not override identity, safety, or epistemic boundaries.",
        manual,
    ))

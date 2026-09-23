"""M6-only test expectation contract, deliberately separate from production."""
from __future__ import annotations

from dataclasses import dataclass

from app.models.autonomy_intention import IntentionType, TargetKind
from app.models.autonomy_decision import AutonomyReasonCode
from app.models.autonomy_intention import AutonomyIntention


@dataclass(frozen=True, slots=True)
class IntentionExpectation:
    expected_intention_type: IntentionType | None = None
    expected_target_kind: TargetKind | None = None
    required_target_key: str | None = None
    required_reason_codes: tuple[AutonomyReasonCode, ...] = ()


def assert_intention_contract(
    expectation: IntentionExpectation,
    intention: AutonomyIntention | None,
) -> None:
    if expectation.expected_intention_type is None:
        if intention is not None:
            raise AssertionError("unexpected_autonomy_intention")
        return
    if intention is None:
        raise AssertionError("expected_autonomy_intention_missing")
    if intention.intention_type != expectation.expected_intention_type:
        raise AssertionError(f"unexpected_intention_type:{intention.intention_type}")
    if expectation.expected_target_kind is not None and intention.target_kind != expectation.expected_target_kind:
        raise AssertionError(f"unexpected_target_kind:{intention.target_kind}")
    if expectation.required_target_key is not None and intention.target_key != expectation.required_target_key:
        raise AssertionError("unexpected_target_key")
    missing = set(expectation.required_reason_codes) - set(intention.reason_codes)
    if missing:
        raise AssertionError(f"missing_intention_reasons:{','.join(sorted(map(str, missing)))}")

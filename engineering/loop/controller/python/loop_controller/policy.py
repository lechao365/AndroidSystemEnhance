from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import StageResult, TerminationDecision


def decide_termination(*, max_attempts: int, current_attempt: int, latest_stage: StageResult, previous_failure_codes: list[FailureCode]) -> TerminationDecision:
    if latest_stage.status == "PASS":
        return TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.NONE,
            reason_summary="verification passed",
            can_retry=False,
            should_escalate=False,
        )

    if current_attempt > max_attempts:
        return TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.REPEATED_FAILURE,
            reason_summary="max attempts exceeded",
            can_retry=False,
            should_escalate=True,
        )

    if previous_failure_codes and latest_stage.failure_code == previous_failure_codes[-1]:
        return TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.REPEATED_FAILURE,
            reason_summary="same failure repeated",
            can_retry=False,
            should_escalate=True,
        )

    return TerminationDecision(
        decision="RETRY",
        reason_code=latest_stage.failure_code,
        reason_summary="retry allowed",
        can_retry=True,
        should_escalate=False,
    )

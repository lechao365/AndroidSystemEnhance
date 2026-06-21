from loop_contracts.models import AttemptState, SessionState, StageResult


def apply_stage_result(session: SessionState, *, attempt_index: int, stage_result: StageResult, decision: str) -> SessionState:
    attempt = AttemptState(attempt_index=attempt_index, stage_results=[stage_result], attempt_decision=decision.lower())
    session.attempts.append(attempt)
    session.current_attempt = attempt_index
    session.status = decision
    return session

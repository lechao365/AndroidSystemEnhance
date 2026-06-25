from loop_contracts.models import SessionState


def new_session(session_id: str, workflow_id: str, target: str, max_attempts: int, suite: str = "") -> SessionState:
    return SessionState(
        session_id=session_id,
        workflow_id=workflow_id,
        target=target,
        suite=suite,
        max_attempts=max_attempts,
    )

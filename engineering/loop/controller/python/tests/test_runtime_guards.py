from loop_controller.runtime.guards import evaluate_guard, guard_chain, GuardEvalRequest
from loop_contracts.failure_codes import FailureCode


def _req(guard_name, **kwargs):
    defaults = dict(
        guard_name=guard_name, attempt_count=1, max_attempts=5,
        latest_status="FAIL", latest_failure_code=FailureCode.RUN_FAILED,
        previous_failure_codes=[], current_patch_hash="", previous_patch_hashes=[],
    )
    defaults.update(kwargs)
    return GuardEvalRequest(**defaults)


def test_guard_all_cases_passed():
    r = evaluate_guard(_req("all_cases_passed", latest_status="PASS", latest_failure_code=FailureCode.NONE))
    assert r.matched is True
    assert r.next_node == "DONE_SUCCESS"


def test_guard_all_cases_passed_not_matched_on_fail():
    r = evaluate_guard(_req("all_cases_passed"))
    assert r.matched is False


def test_guard_attempt_limit_reached():
    r = evaluate_guard(_req("attempt_limit_reached", attempt_count=6, max_attempts=5))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_attempt_limit_not_reached():
    r = evaluate_guard(_req("attempt_limit_reached", attempt_count=3, max_attempts=5))
    assert r.matched is False


def test_guard_repeated_failure_code():
    r = evaluate_guard(_req("repeated_failure_code", previous_failure_codes=[FailureCode.RUN_FAILED]))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_repeated_failure_not_matched_with_different_codes():
    r = evaluate_guard(_req("repeated_failure_code", previous_failure_codes=[FailureCode.COMPILE_FAILED]))
    assert r.matched is False


def test_guard_duplicate_patch_hash():
    r = evaluate_guard(_req("duplicate_patch_hash", current_patch_hash="abc", previous_patch_hashes=["abc"]))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_duplicate_patch_not_matched():
    r = evaluate_guard(_req("duplicate_patch_hash", current_patch_hash="abc", previous_patch_hashes=["xyz"]))
    assert r.matched is False


def test_guard_attempts_below_limit():
    r = evaluate_guard(_req("attempts_below_limit", attempt_count=2, max_attempts=5))
    assert r.matched is True
    assert r.next_node == "BUILD_ANALYSIS_REQUEST"


def test_guard_attempts_below_limit_not_matched():
    r = evaluate_guard(_req("attempts_below_limit", attempt_count=5, max_attempts=5))
    assert r.matched is False


def test_guard_patch_rejected():
    r = evaluate_guard(_req("patch_rejected", latest_failure_code=FailureCode.PATCH_REJECTED))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_compile_failed_but_recoverable():
    r = evaluate_guard(_req("compile_failed_but_recoverable", latest_failure_code=FailureCode.COMPILE_FAILED))
    assert r.matched is True
    assert r.next_node == "REVERT_PATCH"


def test_guard_kernel_dead_no_shell():
    r = evaluate_guard(_req("kernel_dead_no_shell", latest_failure_code=FailureCode.KERNEL_DEAD_NO_SHELL))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_deploy_failed_but_recoverable():
    r = evaluate_guard(_req("deploy_failed_but_recoverable", latest_failure_code=FailureCode.DEPLOY_FATAL))
    assert r.matched is True
    assert r.next_node == "REVERT_PATCH"


def test_guard_patch_applied_successfully():
    r = evaluate_guard(_req("patch_applied_successfully", latest_status="APPLIED"))
    assert r.matched is True
    assert r.next_node == "COMPILE_PATCH"


def test_evaluate_guard_unknown_guard():
    r = evaluate_guard(_req("nonexistent_guard"))
    assert r.matched is False
    assert "unknown guard" in r.reason


def test_guard_chain_returns_first_match():
    chain = ["all_cases_passed", "attempt_limit_reached"]
    r = guard_chain(chain, _req("dummy", latest_status="PASS", latest_failure_code=FailureCode.NONE))
    assert r.matched is True
    assert r.next_node == "DONE_SUCCESS"


def test_guard_chain_returns_no_match():
    chain = ["all_cases_passed"]
    r = guard_chain(chain, _req("dummy"))
    assert r.matched is False


def test_guard_rollback_failed():
    r = evaluate_guard(_req("rollback_failed", latest_failure_code=FailureCode.ROLLBACK_FAILED))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_transport_unrecoverable():
    r = evaluate_guard(_req("transport_unrecoverable", latest_failure_code=FailureCode.TRANSPORT_UNRECOVERABLE))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_session_state_corrupted():
    r = evaluate_guard(_req("session_state_corrupted", latest_failure_code=FailureCode.SESSION_STATE_ERROR))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_boot_timeout_no_recovery():
    r = evaluate_guard(_req("boot_timeout_no_recovery", latest_failure_code=FailureCode.BOOT_TIMEOUT_ROLLBACK))
    assert r.matched is True
    assert r.next_node == "ESCALATE_HUMAN"


def test_guard_boot_timeout_kernel_panic():
    r = evaluate_guard(_req("boot_timeout_kernel_panic", latest_failure_code=FailureCode.BOOT_TIMEOUT_ROLLBACK))
    assert r.matched is True
    assert r.next_node == "REVERT_PATCH"

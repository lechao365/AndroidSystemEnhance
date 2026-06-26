from __future__ import annotations

from typing import Callable

from loop_contracts.failure_codes import FailureCode
from loop_controller.runtime.types import GuardEvalRequest, GuardEvalResult, NodeKind

_GUARD_REGISTRY: dict[str, Callable[[GuardEvalRequest], GuardEvalResult]] = {}


def _register(name: str) -> Callable:
    def deco(fn):
        _GUARD_REGISTRY[name] = fn
        return fn
    return deco


@_register("all_cases_passed")
def _guard_all_cases_passed(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_status == "PASS" and req.latest_failure_code == FailureCode.NONE:
        return GuardEvalResult(matched=True, next_node=NodeKind.DONE_SUCCESS.value, reason="verification passed")
    return GuardEvalResult(matched=False)


@_register("attempt_limit_reached")
def _guard_attempt_limit_reached(req: GuardEvalRequest) -> GuardEvalResult:
    if req.attempt_count >= req.max_attempts:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="max attempts exceeded")
    return GuardEvalResult(matched=False)


@_register("repeated_failure_code")
def _guard_repeated_failure_code(req: GuardEvalRequest) -> GuardEvalResult:
    if req.previous_failure_codes and req.latest_failure_code == req.previous_failure_codes[-1]:
        if req.latest_failure_code != FailureCode.NONE:
            return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="same failure repeated")
    return GuardEvalResult(matched=False)


@_register("duplicate_patch_hash")
def _guard_duplicate_patch_hash(req: GuardEvalRequest) -> GuardEvalResult:
    if req.current_patch_hash and req.current_patch_hash in req.previous_patch_hashes:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="duplicate patch detected")
    return GuardEvalResult(matched=False)


@_register("attempts_below_limit")
def _guard_attempts_below_limit(req: GuardEvalRequest) -> GuardEvalResult:
    if req.attempt_count < req.max_attempts:
        return GuardEvalResult(matched=True, next_node=NodeKind.BUILD_ANALYSIS_REQUEST.value, reason="retry allowed")
    return GuardEvalResult(matched=False)


@_register("patch_rejected")
def _guard_patch_rejected(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.PATCH_REJECTED:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="patch rejected by guard")
    return GuardEvalResult(matched=False)


@_register("compile_failed_but_recoverable")
def _guard_compile_failed_but_recoverable(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.COMPILE_FAILED:
        return GuardEvalResult(matched=True, next_node=NodeKind.REVERT_PATCH.value, reason="compile failed, revert")
    return GuardEvalResult(matched=False)


@_register("patch_applied_successfully")
def _guard_patch_applied_successfully(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_status == "APPLIED":
        return GuardEvalResult(matched=True, next_node=NodeKind.COMPILE_PATCH.value, reason="patch applied, compile")
    return GuardEvalResult(matched=False)


@_register("kernel_dead_no_shell")
def _guard_kernel_dead_no_shell(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.KERNEL_DEAD_NO_SHELL:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="kernel dead, no serial shell")
    return GuardEvalResult(matched=False)


@_register("deploy_failed_but_recoverable")
def _guard_deploy_failed_but_recoverable(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.DEPLOY_FATAL:
        return GuardEvalResult(matched=True, next_node=NodeKind.REVERT_PATCH.value, reason="deploy failed, rollback")
    return GuardEvalResult(matched=False)


@_register("boot_timeout_kernel_panic")
def _guard_boot_timeout_kernel_panic(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.BOOT_TIMEOUT_ROLLBACK:
        return GuardEvalResult(matched=True, next_node=NodeKind.REVERT_PATCH.value, reason="boot timeout/kernel panic, attempt rollback")
    return GuardEvalResult(matched=False)


@_register("rollback_failed")
def _guard_rollback_failed(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.ROLLBACK_FAILED:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="rollback failed, escalate")
    return GuardEvalResult(matched=False)


@_register("transport_unrecoverable")
def _guard_transport_unrecoverable(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.TRANSPORT_UNRECOVERABLE:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="transport unrecoverable")
    return GuardEvalResult(matched=False)


@_register("session_state_corrupted")
def _guard_session_state_corrupted(req: GuardEvalRequest) -> GuardEvalResult:
    if req.latest_failure_code == FailureCode.SESSION_STATE_ERROR:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="session state corrupted")
    return GuardEvalResult(matched=False)


@_register("boot_timeout_no_recovery")
def _guard_boot_timeout_no_recovery(req: GuardEvalRequest) -> GuardEvalResult:
    # 检查历史中是否出现过 BOOT_TIMEOUT_ROLLBACK（说明已尝试过回滚仍无效）
    if FailureCode.BOOT_TIMEOUT_ROLLBACK in req.previous_failure_codes:
        return GuardEvalResult(matched=True, next_node=NodeKind.ESCALATE_HUMAN.value, reason="boot timeout, rollback exhausted")
    return GuardEvalResult(matched=False)


def evaluate_guard(req: GuardEvalRequest) -> GuardEvalResult:
    handler = _GUARD_REGISTRY.get(req.guard_name)
    if handler is None:
        return GuardEvalResult(matched=False, reason=f"unknown guard: {req.guard_name}")
    return handler(req)


def guard_chain(guard_names: list[str], req: GuardEvalRequest) -> GuardEvalResult:
    for name in guard_names:
        result = evaluate_guard(GuardEvalRequest(
            guard_name=name,
            attempt_count=req.attempt_count,
            max_attempts=req.max_attempts,
            latest_status=req.latest_status,
            latest_failure_code=req.latest_failure_code,
            previous_failure_codes=req.previous_failure_codes,
            current_patch_hash=req.current_patch_hash,
            previous_patch_hashes=req.previous_patch_hashes,
        ))
        if result.matched:
            result.guard_name = name
            return result
    return GuardEvalResult(matched=False)

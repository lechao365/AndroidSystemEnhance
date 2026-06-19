"""V1 规则集（6 条）。

每条规则接收 :class:`ObservationSnapshot` + :class:`WorkflowConfig`，
返回 :class:`RuleMatch`。

规则判定依据（不引入 DSL）：
- 文本特征（panic_markers / prompt_markers）
- 时间窗口（quiet_window / observe_timeout）
- 阶段推进失败（prompt 未出现 / boot cycle 超阈值）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from boot_failure_debug.models import RuleMatch

if TYPE_CHECKING:
    from boot_failure_debug.config import WorkflowConfig
    from boot_failure_debug.observer import ObservationSnapshot

# 分类优先级（高 -> 低）
RULE_PRIORITY: list[str] = [
    "kernel_panic_detected",
    "reboot_loop_detected",
    "shell_prompt_available",
    "kernel_boot_hang",
    "login_prompt_not_reached",
    "no_output_after_attach",
]


# ============================================================================
# 单条规则
# ============================================================================

def match_no_output_after_attach(
    snap: "ObservationSnapshot", cfg: "WorkflowConfig"
) -> RuleMatch:
    """规则 1：attach 后无输出（或仅有占位行）。

    判定：有效行数为 0，或仅含 ``__NO_OUTPUT__`` 占位。
    """
    real_lines = [
        line for line in snap.lines if "__NO_OUTPUT__" not in line.text
    ]
    matched = len(real_lines) == 0
    evidence = ["(no output after attach)"] if matched else []
    return RuleMatch(
        rule_id="no_output_after_attach",
        matched=matched,
        confidence=0.9 if matched else 0.0,
        severity="high",
        evidence=evidence,
        phase="OBSERVE_BOOT",
        suggested_actions=["extend_observe_window"] if matched else [],
    )


def match_kernel_panic_detected(
    snap: "ObservationSnapshot", cfg: "WorkflowConfig"
) -> RuleMatch:
    """规则 2：检测到 kernel panic 文本特征。"""
    hits: list[str] = []
    for line in snap.lines:
        for marker in cfg.panic_markers:
            if marker in line.text:
                hits.append(line.text)
                break
    matched = len(hits) > 0
    return RuleMatch(
        rule_id="kernel_panic_detected",
        matched=matched,
        confidence=0.95 if matched else 0.0,
        severity="high",
        evidence=hits[:5],
        phase="CLASSIFY_FAILURE",
        suggested_actions=["capture_recent_context"] if matched else [],
    )


def match_kernel_boot_hang(
    snap: "ObservationSnapshot", cfg: "WorkflowConfig"
) -> RuleMatch:
    """规则 3：boot hang。

    判定：有 boot 输出但无 panic，无 prompt，且静默时间 >= quiet_window。
    """
    # 如果已命中 panic 或有 prompt，则不是 hang
    has_panic = any(
        marker in line.text
        for line in snap.lines
        for marker in cfg.panic_markers
    )
    has_prompt = snap.prompt_line is not None
    if has_panic or has_prompt:
        return RuleMatch(
            rule_id="kernel_boot_hang",
            matched=False,
            confidence=0.0,
            severity="medium",
            evidence=[],
            phase="CLASSIFY_FAILURE",
            suggested_actions=[],
        )

    # 有 boot 输出但静默时间过长
    real_lines = [
        line for line in snap.lines if "__NO_OUTPUT__" not in line.text
    ]
    has_boot_output = any(
        marker in line.text
        for line in real_lines
        for marker in (cfg.boot_markers + cfg.hang_markers)
    )
    matched = has_boot_output and snap.quiet_for_sec >= cfg.quiet_window_sec
    evidence = []
    if matched:
        evidence = [line.text for line in real_lines[-3:]]
    return RuleMatch(
        rule_id="kernel_boot_hang",
        matched=matched,
        confidence=0.8 if matched else 0.0,
        severity="medium",
        evidence=evidence,
        phase="CLASSIFY_FAILURE",
        suggested_actions=["send_enter", "extend_observe_window"] if matched else [],
    )


def match_login_prompt_not_reached(
    snap: "ObservationSnapshot", cfg: "WorkflowConfig"
) -> RuleMatch:
    """规则 4：login/shell prompt 不可达。

    判定：有输出但未出现 prompt marker。
    规则层如实判定；分类层通过优先级排序（panic > login_prompt_not_reached）。
    """
    has_output = any(
        "__NO_OUTPUT__" not in line.text for line in snap.lines
    )
    has_prompt = snap.prompt_line is not None
    # 无输出不算 login_prompt_not_reached（那是 no_output_after_attach）
    if has_prompt or not has_output:
        return RuleMatch(
            rule_id="login_prompt_not_reached",
            matched=False,
            confidence=0.0,
            severity="medium",
            evidence=[],
            phase="CLASSIFY_FAILURE",
            suggested_actions=[],
        )
    matched = True
    evidence = [line.text for line in snap.lines if "__NO_OUTPUT__" not in line.text][-3:]
    return RuleMatch(
        rule_id="login_prompt_not_reached",
        matched=matched,
        confidence=0.75,
        severity="medium",
        evidence=evidence,
        phase="CLASSIFY_FAILURE",
        suggested_actions=["send_enter", "wait_prompt"],
    )


def match_shell_prompt_available(
    snap: "ObservationSnapshot", cfg: "WorkflowConfig"
) -> RuleMatch:
    """规则 5：shell prompt 可达。

    判定：观察窗口内出现 prompt marker。
    """
    matched = snap.prompt_line is not None
    evidence = [snap.prompt_line.text] if matched and snap.prompt_line else []
    return RuleMatch(
        rule_id="shell_prompt_available",
        matched=matched,
        confidence=0.95 if matched else 0.0,
        severity="low",
        evidence=evidence,
        phase="CLASSIFY_FAILURE",
        suggested_actions=["collect_read_only"] if matched else [],
    )


def match_reboot_loop_detected(
    snap: "ObservationSnapshot", cfg: "WorkflowConfig"
) -> RuleMatch:
    """规则 6：反复重启。

    判定：boot_cycle_count >= reboot_loop_threshold。
    """
    from boot_failure_debug.boot_cycles import count_boot_cycles

    cycle_count = count_boot_cycles(snap.lines)
    matched = cycle_count >= cfg.reboot_loop_threshold
    evidence = []
    if matched:
        evidence = [
            f"boot_cycle_count={cycle_count} >= threshold={cfg.reboot_loop_threshold}"
        ]
    return RuleMatch(
        rule_id="reboot_loop_detected",
        matched=matched,
        confidence=0.9 if matched else 0.0,
        severity="high",
        evidence=evidence,
        phase="CLASSIFY_FAILURE",
        suggested_actions=["capture_recent_context"] if matched else [],
    )


# ============================================================================
# 汇总
# ============================================================================

def evaluate_rules(
    snap: "ObservationSnapshot", cfg: "WorkflowConfig"
) -> list[RuleMatch]:
    """运行全部 6 条 V1 规则。

    Returns:
        6 条 RuleMatch 列表（不论是否命中）
    """
    return [
        match_no_output_after_attach(snap, cfg),
        match_kernel_panic_detected(snap, cfg),
        match_kernel_boot_hang(snap, cfg),
        match_login_prompt_not_reached(snap, cfg),
        match_shell_prompt_available(snap, cfg),
        match_reboot_loop_detected(snap, cfg),
    ]


def classify(matches: list[RuleMatch]) -> str:
    """根据规则匹配结果返回最终分类。

    按 :data:`RULE_PRIORITY` 顺序取第一个命中的规则 ID。
    无命中返回 ``"unknown"``。

    Args:
        matches: :func:`evaluate_rules` 的返回值

    Returns:
        分类字符串（规则 ID 或 ``"unknown"``）
    """
    matched_ids = {m.rule_id for m in matches if getattr(m, "matched", False)}
    for rule_id in RULE_PRIORITY:
        if rule_id in matched_ids:
            return rule_id
    return "unknown"

"""boot-failure V1 规则集（6 条）。

每条规则实现 loop_core.rules.Rule 协议：
- 构造时持有所需配置
- match(snapshot) 返回 RuleMatch

规则判定依据：
- 文本特征（panic_markers / prompt_markers）
- 时间窗口（quiet_window / observe_timeout）
- 阶段推进失败（prompt 未出现 / cycle 超阈值）
"""
from __future__ import annotations

from loop_core.models import RuleMatch

# 分类优先级（高 -> 低）
RULE_PRIORITY: list[str] = [
    "kernel_panic_detected",
    "reboot_loop_detected",
    "shell_prompt_available",
    "kernel_boot_hang",
    "login_prompt_not_reached",
    "no_output_after_attach",
]


class NoOutputAfterAttachRule:
    """规则 1：attach 后无输出。"""

    name = "no_output_after_attach"

    def match(self, snap) -> RuleMatch:
        real_lines = [
            line for line in snap.lines if "__NO_OUTPUT__" not in line.text
        ]
        matched = len(real_lines) == 0
        return RuleMatch(
            rule_id=self.name,
            matched=matched,
            confidence=0.9 if matched else 0.0,
            severity="high",
            evidence=["(no output after attach)"] if matched else [],
            phase="OBSERVE_BOOT",
            suggested_actions=["extend_observe_window"] if matched else [],
        )


class KernelPanicDetectedRule:
    """规则 2：检测到 kernel panic 文本特征。"""

    name = "kernel_panic_detected"

    def __init__(self, panic_markers: list[str]) -> None:
        self._panic_markers = panic_markers

    def match(self, snap) -> RuleMatch:
        hits: list[str] = []
        for line in snap.lines:
            for marker in self._panic_markers:
                if marker in line.text:
                    hits.append(line.text)
                    break
        matched = len(hits) > 0
        return RuleMatch(
            rule_id=self.name,
            matched=matched,
            confidence=0.95 if matched else 0.0,
            severity="high",
            evidence=hits[:5],
            phase="CLASSIFY_FAILURE",
            suggested_actions=["capture_recent_context"] if matched else [],
        )


class KernelBootHangRule:
    """规则 3：boot hang。

    判定：有 boot 输出但无 panic，无 prompt，且静默时间 >= quiet_window。
    """

    name = "kernel_boot_hang"

    def __init__(
        self,
        panic_markers: list[str],
        boot_markers: list[str],
        hang_markers: list[str],
        quiet_window_sec: float,
    ) -> None:
        self._panic_markers = panic_markers
        self._boot_markers = boot_markers
        self._hang_markers = hang_markers
        self._quiet_window_sec = quiet_window_sec

    def match(self, snap) -> RuleMatch:
        has_panic = any(
            marker in line.text
            for line in snap.lines
            for marker in self._panic_markers
        )
        has_prompt = snap.prompt_line is not None
        if has_panic or has_prompt:
            return RuleMatch(
                rule_id=self.name,
                matched=False,
                confidence=0.0,
                severity="medium",
                evidence=[],
                phase="CLASSIFY_FAILURE",
                suggested_actions=[],
            )

        real_lines = [
            line for line in snap.lines if "__NO_OUTPUT__" not in line.text
        ]
        has_boot_output = any(
            marker in line.text
            for line in real_lines
            for marker in (self._boot_markers + self._hang_markers)
        )
        matched = has_boot_output and snap.quiet_for_sec >= self._quiet_window_sec
        evidence = []
        if matched:
            evidence = [line.text for line in real_lines[-3:]]
        return RuleMatch(
            rule_id=self.name,
            matched=matched,
            confidence=0.8 if matched else 0.0,
            severity="medium",
            evidence=evidence,
            phase="CLASSIFY_FAILURE",
            suggested_actions=["send_enter", "extend_observe_window"] if matched else [],
        )


class LoginPromptNotReachedRule:
    """规则 4：login/shell prompt 不可达。"""

    name = "login_prompt_not_reached"

    def match(self, snap) -> RuleMatch:
        has_output = any(
            "__NO_OUTPUT__" not in line.text for line in snap.lines
        )
        has_prompt = snap.prompt_line is not None
        if has_prompt or not has_output:
            return RuleMatch(
                rule_id=self.name,
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
            rule_id=self.name,
            matched=matched,
            confidence=0.75,
            severity="medium",
            evidence=evidence,
            phase="CLASSIFY_FAILURE",
            suggested_actions=["send_enter", "wait_prompt"],
        )


class ShellPromptAvailableRule:
    """规则 5：shell prompt 可达。"""

    name = "shell_prompt_available"

    def match(self, snap) -> RuleMatch:
        matched = snap.prompt_line is not None
        evidence = [snap.prompt_line.text] if matched and snap.prompt_line else []
        return RuleMatch(
            rule_id=self.name,
            matched=matched,
            confidence=0.95 if matched else 0.0,
            severity="low",
            evidence=evidence,
            phase="CLASSIFY_FAILURE",
            suggested_actions=["collect_read_only"] if matched else [],
        )


class RebootLoopDetectedRule:
    """规则 6：反复重启。

    判定：cycle_count >= reboot_loop_threshold。
    """

    name = "reboot_loop_detected"

    def __init__(
        self, reboot_markers: list[str], reboot_loop_threshold: int
    ) -> None:
        self._reboot_markers = reboot_markers
        self._reboot_loop_threshold = reboot_loop_threshold

    def match(self, snap) -> RuleMatch:
        from loop_core.cycles import assign_cycles, count_cycles

        lines = assign_cycles(snap.lines, self._reboot_markers)
        cycle_count = count_cycles(lines)
        matched = cycle_count >= self._reboot_loop_threshold
        evidence = []
        if matched:
            evidence = [
                f"cycle_count={cycle_count} >= threshold={self._reboot_loop_threshold}"
            ]
        return RuleMatch(
            rule_id=self.name,
            matched=matched,
            confidence=0.9 if matched else 0.0,
            severity="high",
            evidence=evidence,
            phase="CLASSIFY_FAILURE",
            suggested_actions=["capture_recent_context"] if matched else [],
        )


def build_rules(cfg) -> list:
    """根据配置构建 boot-failure 规则集。

    Args:
        cfg: BootFailureConfig 实例

    Returns:
        实现 loop_core.rules.Rule 协议的规则实例列表
    """
    return [
        NoOutputAfterAttachRule(),
        KernelPanicDetectedRule(cfg.panic_markers),
        KernelBootHangRule(
            cfg.panic_markers,
            cfg.boot_markers,
            cfg.hang_markers,
            cfg.quiet_window_sec,
        ),
        LoginPromptNotReachedRule(),
        ShellPromptAvailableRule(),
        RebootLoopDetectedRule(cfg.reboot_markers, cfg.reboot_loop_threshold),
    ]


def evaluate_rules(snap, cfg) -> list[RuleMatch]:
    """运行全部 6 条 V1 规则（兼容旧接口）。

    Args:
        snap: ObservationSnapshot
        cfg: BootFailureConfig

    Returns:
        6 条 RuleMatch 列表
    """
    from loop_core.rules import evaluate_rules as core_evaluate

    rules = build_rules(cfg)
    return core_evaluate(snap, rules, phase="CLASSIFY_FAILURE")


def classify(matches: list[RuleMatch]) -> str:
    """根据规则匹配结果返回最终分类。"""
    from loop_core.rules import classify as core_classify

    return core_classify(matches, RULE_PRIORITY)

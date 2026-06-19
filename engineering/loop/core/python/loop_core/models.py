"""loop_core 通用数据模型。

- ObservedLine: 观察到的单行输出（带时间戳与 cycle_id）
- RuleMatch: 规则命中结果
- ActionRecord: workflow 执行的动作记录
- LoopAttempt: 一次完整调试闭环
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ObservedLine:
    """观察到的单行输出。

    Attributes:
        t: 相对时间戳
        text: 文本内容
        cycle_id: 所属 cycle 编号（语义由 workflow 定义，如 boot_cycle / restart_cycle）
    """

    t: float
    text: str
    cycle_id: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleMatch:
    """规则命中结果。

    Attributes:
        rule_id: 规则标识
        matched: 是否命中
        confidence: 置信度 0.0 ~ 1.0
        severity: 严重级别 low / medium / high
        evidence: 命中的证据行列表
        phase: 匹配时所处的状态机阶段
        suggested_actions: 建议的后续动作列表
    """

    rule_id: str
    matched: bool
    confidence: float
    severity: str
    evidence: list[str]
    phase: str
    suggested_actions: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActionRecord:
    """workflow 执行的单个动作记录。

    Attributes:
        action_id: 动作唯一标识
        level: 动作级别 L1（只读采样）/ L2（低风险探测）
        command: 命令名
        reason: 执行原因
        result: 执行结果摘要 PLANNED / OK / SKIP / FAIL
        evidence_ref: 证据文件引用
        output_lines: 动作执行期间采集到的文本证据
        metadata: 动作补充元数据
    """

    action_id: str
    level: str
    command: str
    reason: str
    result: str
    evidence_ref: str | None = None
    output_lines: list[str] = field(default_factory=list)
    metadata: dict[str, str | int | bool | list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LoopAttempt:
    """一次完整调试闭环。

    Attributes:
        attempt_id: 闭环唯一标识
        device_id: 设备标识
        outcome: EXIT_SUCCESS / EXIT_FAILURE
        final_classification: 最终分类
        boot_cycle_count: 检测到的 cycle 数量
        matched_rules: 全部规则匹配结果列表
        actions: 执行的动作列表
        artifacts_dir: 本次闭环的 artifacts 目录路径
        extra_summary_lines: 业务层注入的额外摘要行
    """

    attempt_id: str
    device_id: str
    outcome: str
    final_classification: str
    boot_cycle_count: int
    matched_rules: list[RuleMatch] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    artifacts_dir: str = ""
    extra_summary_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

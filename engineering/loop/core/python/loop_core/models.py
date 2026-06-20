"""loop_core v2 数据模型。

v1 的 RuleMatch/ActionRecord/LoopAttempt 已删除。
v2 使用 TestCaseResult/CollectorResult/EvidenceBundle 体系。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ObservedLine:
    """观察到的单行输出。

    Attributes:
        t: 相对时间戳
        text: 文本内容
        cycle_id: 所属 cycle 编号（语义由调用方定义）
    """

    t: float
    text: str
    cycle_id: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TestCaseResult:
    """单个用例的执行结果。

    Attributes:
        id: 用例标识
        suite: 所属 suite 名
        status: pass / fail / skipped / error
        command: 执行的命令（空命令表示仅探测 prompt）
        output: 命令的完整输出
        output_preview: 输出摘要（前 N 行拼接）
        assertion: 断言规格 {type, value/pattern}
        duration_sec: 执行耗时
        failure_reason: fail 时的原因说明
        skip_reason: skipped 时的原因
        triggered_collectors: fail 时触发的 collector 名称列表
        tags: 用例标签
        error_type: error 状态时的错误类别（如 transport_error、异常类名）
    """

    id: str
    suite: str
    status: str
    command: str = ""
    output: str = ""
    output_preview: str = ""
    assertion: dict = field(default_factory=dict)
    duration_sec: float = 0.0
    failure_reason: str = ""
    skip_reason: str = ""
    triggered_collectors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    error_type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CollectorResult:
    """collector 执行结果。

    Attributes:
        name: collector 名称
        commands: 执行的命令列表
        outputs: 每条命令的输出 [{command, lines, duration_sec}]
        hints: 给 AI 的分析提示
    """

    name: str
    commands: list[str]
    outputs: list[dict]
    hints: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidenceBundle:
    """LE 框架输出给 AI 的证据包。

    Attributes:
        bundle_id: 证据包唯一标识
        device_id: 设备标识
        suite: 执行的 suite 名
        timestamp: ISO8601 时间戳
        summary: 汇总 {total, passed, failed, skipped, overall}
        cases: 全部用例结果
        evidence: collector 名称 -> CollectorResult
        device_profile: 设备配置摘要
    """

    bundle_id: str
    device_id: str
    suite: str
    timestamp: str
    summary: dict
    cases: list[TestCaseResult]
    evidence: dict[str, CollectorResult]
    device_profile: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

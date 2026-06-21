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
        outputs: 每条命令的输出 [{command, lines, duration_sec, error?}]
        hints: 给 AI 的分析提示
        status: ok | degraded | error；任一命令抛 OSError 即降级为 degraded
        partial: True 表示部分命令失败（仍有部分 evidence 可用）
        error: status != ok 时的错误信息（取首个错误）
        artifact_paths: collector 产出的工件路径
        required: True 表示该 collector 失败要让整个 suite FAIL
        failure_code: required collector 失败时写入 summary.failure_code 的错误码
    """

    name: str
    commands: list[str]
    outputs: list[dict]
    hints: str = ""
    status: str = "ok"
    partial: bool = False
    error: str = ""
    artifact_paths: list[str] = field(default_factory=list)
    required: bool = False
    failure_code: str = ""

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
        device_profile: 设备配置摘要（如 device_id、prompt_markers）
        execution_config: 本次执行配置摘要（如 capture_timeout、recent_limit、provider_type）
        warnings: 执行过程中产生的非致命告警信息列表
        serial_context: 串口/transport 上下文摘要（来自 describe_runtime_context）
        runtime_context: 运行时上下文（adb endpoint / serial 通用），与 serial_context 同源
    """

    bundle_id: str
    device_id: str
    suite: str
    timestamp: str
    summary: dict
    cases: list[TestCaseResult]
    evidence: dict[str, CollectorResult]
    device_profile: dict = field(default_factory=dict)
    execution_config: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    serial_context: dict = field(default_factory=dict)
    runtime_context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RebootResult:
    """reboot_and_wait 的返回值。

    Attributes:
        status: "pass"（设备成功回来）/ "fail"（超时或 panic）
        transcript_lines: 整个 reboot 过程采集的串口行（从 reboot 命令到判定设备回来）
        failure_reason: 失败原因（"" / "timeout" / "panic_detected: <line>" / "writer_busy" / "fixture_no_reboot"）
        stage_reached: 达到的阶段：l1_boot_start / l2_init_ready / l3_verified / none
        boot_duration_sec: 从 reboot 命令到 L3 验证通过的耗时（失败时为到失败点的耗时）
    """

    status: str
    transcript_lines: list[str] = field(default_factory=list)
    failure_reason: str = ""
    stage_reached: str = "none"
    boot_duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

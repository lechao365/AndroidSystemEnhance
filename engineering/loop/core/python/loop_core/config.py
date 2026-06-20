"""loop_core 配置工具。

提供：
- DeviceProfile：设备语义（markers / transport 类型 / 默认执行参数）

v1 的 BaseWorkflowConfig / merge_profiles 已删除（v2 不使用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceProfile:
    """设备语义字段（来自 device profile）。

    任何 workflow 都需要这些字段来理解设备输出。
    default_capture_timeout / default_recent_limit 作为 CLI 执行参数的兜底默认值。

    Attributes:
        device_id: 设备标识
        transport: transport 类型（如 serial）
        prompt_markers: shell prompt 标记列表
        boot_markers: boot 完成标记
        reboot_markers: reboot 触发标记
        panic_markers: kernel panic 标记
        hang_markers: 设备 hang 标记
        line_ending: 行结束符
        default_capture_timeout: 默认输出采集超时（秒）
        default_recent_limit: 默认采集行数上限
        serial_snippet_limit: describe_runtime_context 串口片段行数上限
    """

    device_id: str = ""
    transport: str = "serial"
    prompt_markers: list[str] = field(default_factory=list)
    boot_markers: list[str] = field(default_factory=list)
    reboot_markers: list[str] = field(default_factory=list)
    panic_markers: list[str] = field(default_factory=list)
    hang_markers: list[str] = field(default_factory=list)
    line_ending: str = "\n"
    default_capture_timeout: float = 5.0
    default_recent_limit: int = 400
    serial_snippet_limit: int = 40

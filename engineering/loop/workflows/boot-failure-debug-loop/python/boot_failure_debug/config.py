"""boot-failure-debug-loop 配置。

继承 loop_core.BaseWorkflowConfig，添加 boot-failure 专属阈值。
通过 loop_core.merge_profiles 合并 device + workflow profile。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields

from loop_core.config import BaseWorkflowConfig, merge_profiles


@dataclass
class BootFailureConfig(BaseWorkflowConfig):
    """boot-failure workflow 配置。

    继承通用阈值（observe_timeout_sec / capture_window_sec /
    recent_lines_limit / max_reassess_rounds），
    添加 boot-failure 专属字段。
    """

    # device 语义（来自 device profile，合并注入）
    device_id: str = ""
    transport: str = "serial"
    prompt_markers: list[str] = field(default_factory=list)
    boot_markers: list[str] = field(default_factory=list)
    reboot_markers: list[str] = field(default_factory=list)
    panic_markers: list[str] = field(default_factory=list)
    hang_markers: list[str] = field(default_factory=list)
    line_ending: str = "\n"

    # boot-failure 专属阈值（来自 workflow profile）
    quiet_window_sec: int = 8
    prompt_wait_sec: int = 12
    reboot_loop_threshold: int = 2
    l1_commands: list[str] = field(
        default_factory=lambda: ["dmesg", "getprop", "mount", "ps"]
    )
    l2_actions: list[str] = field(
        default_factory=lambda: [
            "send_enter",
            "wait_prompt",
            "retry_read_only_once",
            "extend_observe_window",
        ]
    )


def load_profiles(
    device_profile_path: str,
    workflow_profile_path: str,
    override: dict | None = None,
) -> BootFailureConfig:
    """加载并合并 device/workflow profile 与 override。

    合并优先级：device < workflow < override。

    Args:
        device_profile_path: 设备 profile JSON 路径
        workflow_profile_path: workflow profile JSON 路径
        override: 运行时覆盖字段

    Returns:
        合并后的 BootFailureConfig
    """
    merged = merge_profiles(device_profile_path, workflow_profile_path, override)

    # 只保留 BootFailureConfig 已定义的字段
    valid_keys = {f.name for f in fields(BootFailureConfig)}
    filtered = {k: v for k, v in merged.items() if k in valid_keys}

    return BootFailureConfig(**filtered)

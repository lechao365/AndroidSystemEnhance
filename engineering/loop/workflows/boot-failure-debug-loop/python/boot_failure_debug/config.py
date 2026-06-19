"""boot-failure-debug-loop 配置加载与合并。

混合配置模式（对齐设计规格 §5）：
    provider 默认 < device profile < workflow profile < override

device profile 提供"如何理解这台板子"的语义（prompt/boot/panic markers），
workflow profile 提供 workflow 级别的阈值与动作清单。
最终合并为单一 :class:`WorkflowConfig` 供 runner 消费。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class WorkflowConfig:
    """合并后的 workflow 配置。

    包含 device 语义字段与 workflow 阈值字段。
    """

    # device 语义（来自 device profile）
    device_id: str = ""
    transport: str = "serial"
    prompt_markers: list[str] = field(default_factory=list)
    boot_markers: list[str] = field(default_factory=list)
    reboot_markers: list[str] = field(default_factory=list)
    panic_markers: list[str] = field(default_factory=list)
    hang_markers: list[str] = field(default_factory=list)
    line_ending: str = "\n"

    # workflow 阈值（来自 workflow profile）
    observe_timeout_sec: int = 90
    quiet_window_sec: int = 8
    prompt_wait_sec: int = 12
    capture_window_sec: int = 5
    recent_lines_limit: int = 400
    reboot_loop_threshold: int = 2
    max_reassess_rounds: int = 1
    l1_commands: list[str] = field(default_factory=lambda: ["dmesg", "getprop", "mount", "ps"])
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
) -> WorkflowConfig:
    """加载并合并 device/workflow profile 与 override。

    合并优先级：device < workflow < override。

    Args:
        device_profile_path: 设备 profile JSON 路径
        workflow_profile_path: workflow profile JSON 路径
        override: 运行时覆盖字段（命令行 --override-json 解析后的 dict）

    Returns:
        合并后的 :class:`WorkflowConfig`

    Raises:
        FileNotFoundError: profile 文件不存在
        json.JSONDecodeError: profile JSON 格式错误
    """
    device = json.loads(Path(device_profile_path).read_text(encoding="utf-8"))
    workflow = json.loads(Path(workflow_profile_path).read_text(encoding="utf-8"))
    merged: dict = {**device, **workflow, **(override or {})}

    # 只保留 WorkflowConfig 已定义的字段，避免未知字段触发 TypeError
    valid_keys = {f.name for f in fields(WorkflowConfig)}
    filtered = {k: v for k, v in merged.items() if k in valid_keys}

    return WorkflowConfig(**filtered)

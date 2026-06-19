"""loop_core 配置工具。

提供：
- DeviceProfile：设备语义（markers / transport 类型等）
- BaseWorkflowConfig：跨 workflow 通用的阈值基类
- merge_profiles：通用 profile 合并工具

具体 workflow 继承 BaseWorkflowConfig 添加自己的阈值字段。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DeviceProfile:
    """设备语义字段（来自 device profile）。

    任何 workflow 都需要这些字段来理解设备输出。
    """

    device_id: str = ""
    transport: str = "serial"
    prompt_markers: list[str] = field(default_factory=list)
    boot_markers: list[str] = field(default_factory=list)
    reboot_markers: list[str] = field(default_factory=list)
    panic_markers: list[str] = field(default_factory=list)
    hang_markers: list[str] = field(default_factory=list)
    line_ending: str = "\n"


@dataclass
class BaseWorkflowConfig:
    """跨 workflow 通用的阈值基类。

    具体 workflow 继承此类添加自己的阈值字段。
    只包含确定所有 workflow 都需要的通用观察/采样阈值。
    """

    observe_timeout_sec: int = 90
    capture_window_sec: int = 5
    recent_lines_limit: int = 400
    max_reassess_rounds: int = 1


def merge_profiles(
    device_profile_path: str,
    workflow_profile_path: str,
    override: dict | None = None,
) -> dict:
    """合并 device profile + workflow profile + override。

    合并优先级：device < workflow < override。

    Args:
        device_profile_path: 设备 profile JSON 路径
        workflow_profile_path: workflow profile JSON 路径
        override: 运行时覆盖字段

    Returns:
        合并后的 dict（未绑定具体类型，由调用方消费）

    Raises:
        FileNotFoundError: profile 文件不存在
        json.JSONDecodeError: profile JSON 格式错误
    """
    device = json.loads(Path(device_profile_path).read_text(encoding="utf-8"))
    workflow = json.loads(Path(workflow_profile_path).read_text(encoding="utf-8"))
    return {**device, **workflow, **(override or {})}

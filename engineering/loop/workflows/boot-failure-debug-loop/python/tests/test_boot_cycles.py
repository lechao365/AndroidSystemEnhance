"""boot_cycles.py 单元测试。

覆盖：
- reboot_markers 触发 cycle 分裂
- 首行 boot_marker 不触发新 cycle
- 连续 reboot markers 计数正确
"""
from pathlib import Path

from boot_failure_debug.boot_cycles import assign_boot_cycles, count_boot_cycles
from boot_failure_debug.config import load_profiles
from boot_failure_debug.models import ObservedLine

REPO = Path(__file__).resolve().parents[6]
DEVICE_PROFILE = REPO / "engineering/loop/connection/profiles/devices/rp5/default.json"
WORKFLOW_PROFILE = REPO / "engineering/loop/profiles/boot-failure-debug/default.json"


def _cfg():
    return load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE))


def test_assign_boot_cycles_single_cycle_no_reboot():
    cfg = _cfg()
    lines = [
        ObservedLine(t=0.0, text="Booting Linux"),
        ObservedLine(t=2.0, text="console:/ $"),
    ]
    result = assign_boot_cycles(lines, cfg)
    assert all(line.boot_cycle_id == 1 for line in result)


def test_assign_boot_cycles_splits_on_reboot_marker():
    cfg = _cfg()
    lines = [
        ObservedLine(t=0.0, text="Booting Linux"),
        ObservedLine(t=4.0, text="console:/ $"),
        ObservedLine(t=10.0, text="reboot: Restarting system"),
        ObservedLine(t=12.0, text="Booting Linux"),
    ]
    result = assign_boot_cycles(lines, cfg)
    # reboot marker 行自身归到当前 cycle，下一行开始新 cycle
    assert result[0].boot_cycle_id == 1
    assert result[1].boot_cycle_id == 1
    assert result[2].boot_cycle_id == 1  # reboot marker 行归当前 cycle
    assert result[3].boot_cycle_id == 2  # 下一个 boot 开始新 cycle


def test_assign_boot_cycles_multiple_reboots():
    cfg = _cfg()
    lines = [
        ObservedLine(t=0.0, text="Booting Linux"),
        ObservedLine(t=3.0, text="reboot: Restarting system"),
        ObservedLine(t=4.0, text="Booting Linux"),
        ObservedLine(t=7.0, text="reboot: Restarting system"),
        ObservedLine(t=8.0, text="Booting Linux"),
    ]
    result = assign_boot_cycles(lines, cfg)
    assert result[0].boot_cycle_id == 1
    assert result[1].boot_cycle_id == 1
    assert result[2].boot_cycle_id == 2
    assert result[3].boot_cycle_id == 2
    assert result[4].boot_cycle_id == 3


def test_assign_boot_cycles_empty_lines():
    cfg = _cfg()
    result = assign_boot_cycles([], cfg)
    assert result == []


def test_assign_boot_cycles_first_line_reboot_does_not_increment():
    """首行就是 reboot marker 不应产生 cycle 0。"""
    cfg = _cfg()
    lines = [
        ObservedLine(t=0.0, text="reboot: Restarting system"),
        ObservedLine(t=1.0, text="Booting Linux"),
    ]
    result = assign_boot_cycles(lines, cfg)
    # 首行没有 out，不触发分裂
    assert result[0].boot_cycle_id == 1
    assert result[1].boot_cycle_id == 1


def test_count_boot_cycles_returns_max_cycle_id():
    cfg = _cfg()
    lines = [
        ObservedLine(t=0.0, text="Booting Linux"),
        ObservedLine(t=3.0, text="reboot: Restarting system"),
        ObservedLine(t=4.0, text="Booting Linux"),
        ObservedLine(t=7.0, text="reboot: Restarting system"),
        ObservedLine(t=8.0, text="Booting Linux"),
    ]
    assigned = assign_boot_cycles(lines, cfg)
    assert count_boot_cycles(assigned) == 3


def test_count_boot_cycles_empty_returns_zero():
    assert count_boot_cycles([]) == 0

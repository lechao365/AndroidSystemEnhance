"""loop_core/cycles.py 单元测试。"""
from loop_core.cycles import assign_cycles, count_cycles
from loop_core.models import ObservedLine


def _lines(texts: list[str]) -> list[ObservedLine]:
    return [ObservedLine(t=float(i), text=t) for i, t in enumerate(texts)]


def test_assign_cycles_single_cycle_no_marker():
    lines = _lines(["line1", "line2", "line3"])
    result = assign_cycles(lines, ["reboot"])
    assert all(line.cycle_id == 1 for line in result)


def test_assign_cycles_splits_on_marker():
    lines = _lines(["boot1", "reboot marker", "boot2"])
    result = assign_cycles(lines, ["reboot marker"])
    assert result[0].cycle_id == 1  # boot1
    assert result[1].cycle_id == 1  # reboot marker 归当前 cycle
    assert result[2].cycle_id == 2  # boot2 是新 cycle


def test_assign_cycles_multiple_markers():
    lines = _lines(["a", "reboot", "b", "reboot", "c"])
    result = assign_cycles(lines, ["reboot"])
    assert [r.cycle_id for r in result] == [1, 1, 2, 2, 3]


def test_assign_cycles_first_line_marker_does_not_increment():
    """首行就是 marker，视为冷启动，不分裂。"""
    lines = _lines(["reboot", "boot"])
    result = assign_cycles(lines, ["reboot"])
    assert result[0].cycle_id == 1
    assert result[1].cycle_id == 1


def test_assign_cycles_empty_lines():
    result = assign_cycles([], ["reboot"])
    assert result == []


def test_count_cycles_returns_max_cycle_id():
    lines = _lines(["a", "b"])
    lines = assign_cycles(lines, ["reboot"])
    # 单 cycle，count = 1
    assert count_cycles(lines) == 1


def test_count_cycles_multiple():
    lines = _lines(["a", "reboot", "b", "reboot", "c"])
    lines = assign_cycles(lines, ["reboot"])
    assert count_cycles(lines) == 3


def test_count_cycles_empty_returns_zero():
    assert count_cycles([]) == 0


def test_assign_cycles_preserves_text_and_t():
    lines = _lines(["hello", "world"])
    result = assign_cycles(lines, ["reboot"])
    assert result[0].text == "hello"
    assert result[1].text == "world"
    assert result[0].t == 0.0
    assert result[1].t == 1.0

"""lcview 专属 ScriptedAnalyzer 规则测试。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loop_controller.analyzer_protocol import (
    _rule_lcview_hal_connect_fault,
    _rule_lcview_parse_loop_break,
    _rule_lcview_rc_fault_prop,
)


def test_hal_connect_fault_match_connect_failed():
    """failure_reason 含 'connect failed' 且涉及 lcview_hal 时命中。"""
    case = {
        "failure_reason": "lcview_hal_service_state: HAL connect failed: cannot cast to ILcView",
        "command": "getprop init.svc.lechao_lcview_hal",
    }
    result = _rule_lcview_hal_connect_fault(case)
    assert result is not None, "should match"
    assert len(result) == 1
    assert "lechao_lcview.cpp" in result[0].workspace_path


def test_hal_connect_fault_match_cannot_cast():
    """failure_reason 含 'cannot cast to ILcView' 时命中。"""
    case = {
        "failure_reason": "HAL daemon reports: cannot cast to ILcView",
        "command": "getprop init.svc.lechao_lcview_hal",
    }
    result = _rule_lcview_hal_connect_fault(case)
    assert result is not None


def test_hal_connect_fault_no_match_unrelated():
    """无关 failure_reason 不命中。"""
    case = {"failure_reason": "some unrelated error", "command": "getprop"}
    assert _rule_lcview_hal_connect_fault(case) is None


def test_hal_connect_fault_no_match_no_lcview():
    """不含 lcview 关键词的不命中。"""
    case = {"failure_reason": "connect failed somewhere", "command": "getprop init.svc.other"}
    assert _rule_lcview_hal_connect_fault(case) is None


def test_hal_connect_fault_match_by_case_id():
    """verify 用例 lcview_no_validate_errors 失败时（计数非 0）应命中。

    场景：verify command 是 grep|wc -l，failure_reason 只有计数，
    但 case_id 暗示了 validate 错误存在。
    """
    case = {
        "id": "lcview_no_validate_errors",
        "failure_reason": "expected output to contain '0', got: 1",
        "command": "logcat -d -s lechao_lcview:* 2>/dev/null | grep -E 'fault injected|validate failed: bad magic' | wc -l",
        "output": "1",
    }
    result = _rule_lcview_hal_connect_fault(case)
    assert result is not None, "should match by case_id"
    assert len(result) == 1
    assert "lechao_lcview.cpp" in result[0].workspace_path


# ---------------------------------------------------------------------------
# N1：解析循环 break 故障（KB-miss → 新增 ScriptedAnalyzer 规则）
# ---------------------------------------------------------------------------

def test_parse_loop_break_match_by_case_id():
    """专属 verify 用例 lcview_no_readloop_abort 失败（计数非 0）时命中。"""
    case = {
        "id": "lcview_no_readloop_abort",
        "failure_reason": "expected output to contain '0', got: 1",
        "command": "logcat -d -s lechao_lcview:* 2>/dev/null | grep -c 'parse loop aborted: read-loop fault N1'",
        "output": "1",
    }
    result = _rule_lcview_parse_loop_break(case)
    assert result is not None, "should match by case_id"
    assert len(result) == 1
    assert "lechao_lcview.cpp" in result[0].workspace_path
    assert result[0].new_content == "", "fix is to delete the injected lines"


def test_parse_loop_break_match_by_text():
    """failure_reason 含特异故障文本时命中。"""
    case = {
        "id": "some_other_case",
        "failure_reason": "lechao_lcview: parse loop aborted: read-loop fault N1",
        "command": "logcat",
    }
    result = _rule_lcview_parse_loop_break(case)
    assert result is not None


def test_parse_loop_break_no_match_unrelated():
    """无关 case 不命中（case_id 不匹配 + 无特异文本 + 计数为 0）。"""
    case = {
        "id": "lcview_no_readloop_abort",
        "failure_reason": "some unrelated error",
        "command": "logcat",
        "output": "0",
    }
    assert _rule_lcview_parse_loop_break(case) is None


def test_parse_loop_break_no_match_other_case():
    """其它 case_id 且无特异文本时不命中（避免与 F2 等规则交叉误判）。"""
    case = {
        "id": "lcview_no_validate_errors",
        "failure_reason": "connect failed: cannot cast to ILcView",
        "command": "getprop",
        "output": "1",
    }
    assert _rule_lcview_parse_loop_break(case) is None


# ---------------------------------------------------------------------------
# N6：init.rc 注入故障属性（DD_BOOT_REBOOT 链路验证）
# ---------------------------------------------------------------------------

def test_rc_fault_prop_match_by_case_id():
    """专属 verify 用例 lcview_no_n6_fault_prop 失败（getprop 非空）时命中，删除 .rc setprop 行。"""
    case = {
        "id": "lcview_no_n6_fault_prop",
        "failure_reason": "expected output to equal '', got: injected",
        "command": "getprop lechao.fault.n6",
        "output": "injected",
    }
    result = _rule_lcview_rc_fault_prop(case)
    assert result is not None, "should match by case_id"
    assert len(result) == 1
    assert result[0].workspace_path.endswith("lechao_lcview.rc")
    assert result[0].new_content == ""


def test_rc_fault_prop_match_by_text():
    """failure_reason 含 lechao.fault.n6 时命中。"""
    case = {"id": "x", "failure_reason": "boot property lechao.fault.n6 set unexpectedly", "command": "getprop"}
    assert _rule_lcview_rc_fault_prop(case) is not None


def test_rc_fault_prop_no_match():
    """无关 case（case_id 不匹配 + 无特异文本 + output 空）不命中。"""
    case = {"id": "lcview_no_n6_fault_prop", "failure_reason": "unrelated", "command": "getprop", "output": ""}
    assert _rule_lcview_rc_fault_prop(case) is None

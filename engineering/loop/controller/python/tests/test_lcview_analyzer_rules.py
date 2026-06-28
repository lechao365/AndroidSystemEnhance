"""lcview 专属 ScriptedAnalyzer 规则测试。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loop_controller.analyzer_protocol import _rule_lcview_hal_connect_fault


def test_hal_connect_fault_match_connect_failed():
    """failure_reason 含 'connect failed' 且涉及 lcview_hal 时命中。"""
    case = {
        "failure_reason": "lcview_hal_service_state: HAL connect failed: cannot cast to ILcView",
        "command": "getprop init.svc.lechao_lcview_hal",
    }
    result = _rule_lcview_hal_connect_fault(case)
    assert result is not None, "should match"
    assert len(result) == 1
    assert "LcView.cpp" in result[0].workspace_path


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

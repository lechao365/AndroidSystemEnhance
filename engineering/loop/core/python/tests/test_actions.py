"""loop_core/actions.py 单元测试。"""
from unittest.mock import MagicMock

from loop_core.actions import execute_actions
from loop_core.models import ActionRecord


def _stub_execute_fn(action, transport):
    """测试用执行函数：把 result 改成 OK，加一个标记。"""
    return ActionRecord(
        action_id=action.action_id,
        level=action.level,
        command=action.command,
        reason=action.reason,
        result="OK",
        evidence_ref=action.evidence_ref,
        output_lines=list(action.output_lines),
        metadata={"executed_by": "stub"},
    )


def test_execute_actions_calls_fn_for_each():
    actions = [
        ActionRecord("a-1", "L1", "dmesg", "reason", "PLANNED"),
        ActionRecord("a-2", "L1", "getprop", "reason", "PLANNED"),
    ]
    transport = MagicMock()
    result = execute_actions(actions, transport, _stub_execute_fn)
    assert len(result) == 2
    assert all(a.result == "OK" for a in result)
    assert all(a.metadata["executed_by"] == "stub" for a in result)


def test_execute_actions_preserves_order():
    actions = [
        ActionRecord("a-1", "L1", "dmesg", "reason", "PLANNED"),
        ActionRecord("a-2", "L1", "getprop", "reason", "PLANNED"),
    ]
    transport = MagicMock()
    result = execute_actions(actions, transport, _stub_execute_fn)
    assert [a.command for a in result] == ["dmesg", "getprop"]


def test_execute_actions_empty():
    transport = MagicMock()
    result = execute_actions([], transport, _stub_execute_fn)
    assert result == []


def test_execute_actions_passes_transport_to_fn():
    """确认 execute_fn 能拿到 transport 实例。"""
    actions = [ActionRecord("a-1", "L1", "dmesg", "reason", "PLANNED")]
    transport = MagicMock()
    received_transport = []

    def capture_fn(action, t):
        received_transport.append(t)
        return ActionRecord("a-1", "L1", "dmesg", "reason", "OK")

    execute_actions(actions, transport, capture_fn)
    assert received_transport[0] is transport

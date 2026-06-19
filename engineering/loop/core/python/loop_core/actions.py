"""loop_core 动作批量执行器。

只提供通用的 execute_actions 循环；
具体的动作分派逻辑由业务层通过 execute_fn 注入。
"""
from __future__ import annotations

from typing import Callable

from loop_core.models import ActionRecord


ExecuteFn = Callable[["ActionRecord", object], ActionRecord]
"""动作执行函数签名。

Args:
    action: 待执行的动作
    transport: transport 实例

Returns:
    更新后的 ActionRecord
"""


def execute_actions(
    actions: list[ActionRecord],
    transport,
    execute_fn: ExecuteFn,
) -> list[ActionRecord]:
    """执行动作列表并返回更新后的结果。

    Args:
        actions: 动作列表（result=PLANNED）
        transport: transport 实例
        execute_fn: 业务层注入的动作分派函数

    Returns:
        更新后的动作列表
    """
    return [execute_fn(a, transport) for a in actions]

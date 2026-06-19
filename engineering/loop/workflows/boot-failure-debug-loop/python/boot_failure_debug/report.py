"""报告生成：人类可读摘要 + 机器可读 JSON。

每轮 attempt 输出（对齐设计规格 §10.6）：
- 最终分类
- 启动推进阶段
- boot cycle 次数
- 命中规则
- 执行动作
- 关键证据
- 建议下一步
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from boot_failure_debug.models import LoopAttempt


def render_summary(attempt: "LoopAttempt") -> str:
    """生成人类可读的文本摘要。

    Args:
        attempt: 闭环结果

    Returns:
        多行文本摘要
    """
    matched_rule_ids = [
        m.rule_id for m in attempt.matched_rules if getattr(m, "matched", False)
    ]
    action_cmds = [a.command for a in attempt.actions]
    evidence_summary = []
    for m in attempt.matched_rules:
        if getattr(m, "matched", False) and m.evidence:
            evidence_summary.extend(m.evidence[:2])

    lines = [
        f"最终分类: {attempt.final_classification}",
        f"结果: {attempt.outcome}",
        f"boot_cycle: {attempt.boot_cycle_count}",
        f"命中规则: {', '.join(matched_rule_ids) if matched_rule_ids else '(无)'}",
        f"执行动作: {', '.join(action_cmds) if action_cmds else '(无)'}",
        f"关键证据: {', '.join(evidence_summary[:5]) if evidence_summary else '(无)'}",
    ]

    # 建议下一步
    if attempt.outcome == "EXIT_SUCCESS":
        lines.append("建议下一步: 系统正常启动，可继续正常开发流程")
    elif attempt.final_classification == "kernel_panic_detected":
        lines.append("建议下一步: 检查 kernel panic 日志定位崩溃模块")
    elif attempt.final_classification == "reboot_loop_detected":
        lines.append("建议下一步: 分析 boot cycle 边界，检查 early boot 失败点")
    elif attempt.final_classification == "no_output_after_attach":
        lines.append("建议下一步: 检查串口连接、设备上电状态")
    else:
        lines.append("建议下一步: 根据分类进一步排查")

    return "\n".join(lines)


def write_report_bundle(
    attempt: "LoopAttempt",
    output_dir: str,
    snapshot_lines: list[str] | None = None,
) -> dict[str, str]:
    """生成报告 bundle：JSON + TXT + captured_lines。

    Args:
        attempt: 闭环结果
        output_dir: artifacts 目录路径
        snapshot_lines: 采样到的原始行列表（可选）

    Returns:
        生成的文件路径 dict
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}

    # report.json
    report_json = out / "report.json"
    report_json.write_text(json.dumps(attempt.to_dict(), indent=2, ensure_ascii=False))
    paths["report_json"] = str(report_json)

    # summary.txt
    summary_txt = out / "summary.txt"
    summary_txt.write_text(render_summary(attempt))
    paths["summary_txt"] = str(summary_txt)

    # captured_lines.txt（如果提供）
    if snapshot_lines:
        captured_txt = out / "captured_lines.txt"
        captured_txt.write_text("\n".join(snapshot_lines))
        paths["captured_lines_txt"] = str(captured_txt)

    return paths
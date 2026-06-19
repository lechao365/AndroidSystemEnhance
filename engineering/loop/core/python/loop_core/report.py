"""loop_core 报告渲染。

提供：
- write_report_bundle：写入 JSON + TXT + captured_lines
- render_summary：渲染通用摘要（不假设 boot_cycle）

render_summary 通过 attempt.extra_summary_lines 追加业务特有行，
通过 advice_map 注入业务建议。
"""
from __future__ import annotations

import json
from pathlib import Path

from loop_core.models import LoopAttempt


def render_summary(
    attempt: LoopAttempt,
    advice_map: dict[str, str] | None = None,
) -> str:
    """生成人类可读的文本摘要。

    Args:
        attempt: 闭环结果
        advice_map: 分类 -> 建议文案 映射；未提供时使用通用建议

    Returns:
        多行文本摘要
    """
    matched_rule_ids = [
        m.rule_id for m in attempt.matched_rules if getattr(m, "matched", False)
    ]
    action_cmds = [a.command for a in attempt.actions]
    evidence_summary: list[str] = []
    for m in attempt.matched_rules:
        if getattr(m, "matched", False) and m.evidence:
            evidence_summary.extend(m.evidence[:2])

    lines = [
        f"最终分类: {attempt.final_classification}",
        f"结果: {attempt.outcome}",
        f"cycle_count: {attempt.boot_cycle_count}",
        f"命中规则: {', '.join(matched_rule_ids) if matched_rule_ids else '(无)'}",
        f"执行动作: {', '.join(action_cmds) if action_cmds else '(无)'}",
        f"关键证据: {', '.join(evidence_summary[:5]) if evidence_summary else '(无)'}",
    ]

    # L1 采样摘要（从 actions 中提取）
    l1_previews: list[str] = []
    for action in attempt.actions:
        if action.level == "L1" and action.output_lines:
            preview = " | ".join(action.output_lines[:2])
            l1_previews.append(f"{action.command}: {preview}")
    if l1_previews:
        lines.append(f"L1采样: {'; '.join(l1_previews[:4])}")

    # 业务层注入的额外摘要行
    if attempt.extra_summary_lines:
        lines.extend(attempt.extra_summary_lines)

    # 建议
    advice = _build_advice(attempt, advice_map)
    lines.append(f"建议下一步: {advice}")

    return "\n".join(lines)


def _build_advice(
    attempt: LoopAttempt, advice_map: dict[str, str] | None
) -> str:
    """构建建议文案。"""
    if advice_map and attempt.final_classification in advice_map:
        return advice_map[attempt.final_classification]
    if attempt.outcome == "EXIT_SUCCESS":
        return "系统正常启动，可继续正常开发流程"
    return "根据分类进一步排查"


def write_report_bundle(
    attempt: LoopAttempt,
    output_dir: str,
    snapshot_lines: list[str] | None = None,
    advice_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """生成报告 bundle：JSON + TXT + captured_lines。

    Args:
        attempt: 闭环结果
        output_dir: artifacts 目录路径
        snapshot_lines: 采样到的原始行列表（可选）
        advice_map: 分类 -> 建议文案 映射（可选）

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
    summary_txt.write_text(render_summary(attempt, advice_map=advice_map))
    paths["summary_txt"] = str(summary_txt)

    # captured_lines.txt（如果提供）
    if snapshot_lines:
        captured_txt = out / "captured_lines.txt"
        captured_txt.write_text("\n".join(snapshot_lines))
        paths["captured_lines_txt"] = str(captured_txt)

    return paths

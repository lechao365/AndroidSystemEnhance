"""EvidenceBundle JSON 输出。

将 CaseExecutor 产出的 EvidenceBundle 序列化为：
- evidence_bundle.json：完整结构化 JSON（供 AI 分析）
- summary.txt：人类可读摘要
"""
from __future__ import annotations

import json
from pathlib import Path

from loop_core.models import EvidenceBundle


def write_evidence_bundle(bundle: EvidenceBundle, output_dir: str) -> dict[str, str]:
    """将 EvidenceBundle 写入文件。

    Args:
        bundle: 证据包
        output_dir: 输出目录

    Returns:
        生成的文件路径 dict {"evidence_json": ..., "summary_txt": ...}
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # evidence_bundle.json
    json_path = out / "evidence_bundle.json"
    json_path.write_text(
        json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # summary.txt
    summary_path = out / "summary.txt"
    summary_path.write_text(render_evidence_summary(bundle), encoding="utf-8")

    return {
        "evidence_json": str(json_path),
        "summary_txt": str(summary_path),
    }


def render_evidence_summary(bundle: EvidenceBundle) -> str:
    """渲染人类可读的 EvidenceBundle 摘要。

    Args:
        bundle: 证据包

    Returns:
        多行文本摘要
    """
    s = bundle.summary
    lines = [
        f"Suite: {bundle.suite}",
        f"Device: {bundle.device_id}",
        f"Timestamp: {bundle.timestamp}",
        f"Overall: {s['overall']}",
        f"Total: {s['total']}  Passed: {s['passed']}  "
        f"Failed: {s['failed']}  Skipped: {s['skipped']}",
        "",
        "=== 用例结果 ===",
    ]

    for case in bundle.cases:
        status_marker = {"pass": "[PASS]", "fail": "[FAIL]", "skipped": "[SKIP]", "error": "[ERR!]"}.get(
            case.status, "[????]"
        )
        lines.append(f"  {status_marker} {case.id}")
        if case.command:
            lines.append(f"        command: {case.command}")
        if case.failure_reason:
            lines.append(f"        reason: {case.failure_reason[:200]}")
        if case.skip_reason:
            lines.append(f"        reason: {case.skip_reason}")
        if case.triggered_collectors:
            lines.append(f"        collectors: {', '.join(case.triggered_collectors)}")

    if bundle.evidence:
        lines.append("")
        lines.append("=== 采集证据 ===")
        for name, cr in bundle.evidence.items():
            lines.append(f"  [{name}] ({len(cr.commands)} commands)")
            if cr.hints:
                lines.append(f"        hints: {cr.hints}")

    return "\n".join(lines)

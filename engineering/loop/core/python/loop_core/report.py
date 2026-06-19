"""loop_core 报告渲染（v2）。

v2 report.py 是 evidence.py 的薄封装，保持向后兼容的函数名。
实际逻辑在 evidence.py 中。
"""
from __future__ import annotations

from loop_core.evidence import render_evidence_summary, write_evidence_bundle
from loop_core.models import EvidenceBundle


def write_report_bundle(
    bundle: EvidenceBundle,
    output_dir: str,
    **kwargs,
) -> dict[str, str]:
    """写入 EvidenceBundle 报告文件。

    v2 委托 write_evidence_bundle。kwargs 忽略 v1 遗留参数（snapshot_lines/advice_map）。

    Args:
        bundle: EvidenceBundle
        output_dir: 输出目录

    Returns:
        {"evidence_json": ..., "summary_txt": ...}
    """
    return write_evidence_bundle(bundle, output_dir)


def render_summary(bundle: EvidenceBundle, **kwargs) -> str:
    """渲染 EvidenceBundle 摘要文本。

    v2 委托 render_evidence_summary。kwargs 忽略 v1 遗留参数。

    Args:
        bundle: EvidenceBundle

    Returns:
        多行文本摘要
    """
    return render_evidence_summary(bundle)

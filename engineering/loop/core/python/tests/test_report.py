"""report.py v2 测试：验证它委托 evidence.py。"""
import json
from pathlib import Path

from loop_core.models import EvidenceBundle, TestCaseResult
from loop_core.report import write_report_bundle, render_summary


def _make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="eb-rpt",
        device_id="rp5",
        suite="t",
        timestamp="2026-06-19T22:00:00+08:00",
        summary={"total": 1, "passed": 1, "failed": 0, "skipped": 0, "overall": "PASS"},
        cases=[TestCaseResult(id="c1", suite="t", status="pass")],
        evidence={},
    )


def test_write_report_bundle_delegates_to_evidence(tmp_path):
    paths = write_report_bundle(_make_bundle(), str(tmp_path))
    assert "evidence_json" in paths
    assert Path(paths["evidence_json"]).exists()
    assert Path(paths["summary_txt"]).exists()


def test_render_summary_returns_text():
    text = render_summary(_make_bundle())
    assert "Suite: t" in text
    assert "PASS" in text
    assert "c1" in text

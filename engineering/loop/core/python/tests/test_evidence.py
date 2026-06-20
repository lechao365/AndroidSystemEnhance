"""evidence.py 测试：EvidenceBundle JSON 序列化与文件输出。"""
import json
from pathlib import Path

from loop_core.evidence import write_evidence_bundle
from loop_core.models import CollectorResult, EvidenceBundle, TestCaseResult


def _make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="eb-test001",
        device_id="rp5",
        suite="boot-success",
        timestamp="2026-06-19T22:36:06+08:00",
        summary={"total": 2, "passed": 1, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[
            TestCaseResult(id="shell_reachable", suite="boot-success", status="pass"),
            TestCaseResult(
                id="zygote_running",
                suite="boot-success",
                status="fail",
                command="getprop init.svc.zygote",
                failure_reason="expected 'running'",
                triggered_collectors=["crash_dump"],
            ),
        ],
        evidence={
            "crash_dump": CollectorResult(
                name="crash_dump",
                commands=["logcat -b crash -d"],
                outputs=[{"command": "logcat -b crash -d", "lines": ["crash line"]}],
                hints="check abort msg",
            )
        },
    )


def test_write_evidence_bundle_json(tmp_path):
    """write_evidence_bundle 输出合法 JSON。"""
    bundle = _make_bundle()
    paths = write_evidence_bundle(bundle, str(tmp_path))

    assert "evidence_json" in paths
    p = Path(paths["evidence_json"])
    assert p.exists()

    data = json.loads(p.read_text())
    assert data["bundle_id"] == "eb-test001"
    assert data["summary"]["overall"] == "FAIL"
    assert len(data["cases"]) == 2
    assert "crash_dump" in data["evidence"]


def test_write_evidence_bundle_summary_txt(tmp_path):
    """write_evidence_bundle 同时输出 summary.txt。"""
    bundle = _make_bundle()
    paths = write_evidence_bundle(bundle, str(tmp_path))

    assert "summary_txt" in paths
    p = Path(paths["summary_txt"])
    assert p.exists()

    text = p.read_text()
    assert "boot-success" in text
    assert "FAIL" in text
    assert "zygote_running" in text


def test_long_output_truncated_in_json(tmp_path):
    """长输出在 JSON 中被截断（output 保留，但 preview 有限）。"""
    long_output = "x" * 5000
    bundle = EvidenceBundle(
        bundle_id="eb-long",
        device_id="rp5",
        suite="t",
        timestamp="2026-06-19T22:36:06+08:00",
        summary={"total": 1, "passed": 0, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[
            TestCaseResult(
                id="c1",
                suite="t",
                status="fail",
                output=long_output,
                output_preview=long_output[:200],
            ),
        ],
        evidence={},
    )
    paths = write_evidence_bundle(bundle, str(tmp_path))
    data = json.loads(Path(paths["evidence_json"]).read_text())
    assert len(data["cases"][0]["output"]) == 5000  # output 保留完整
    assert len(data["cases"][0]["output_preview"]) == 200


def test_summary_renders_transcript_and_reboot_cycles(tmp_path):
    bundle = EvidenceBundle(
        bundle_id="eb-1",
        device_id="rp5",
        suite="boot-success",
        timestamp="2026-06-20T12:00:00+08:00",
        summary={"total": 1, "passed": 0, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[],
        evidence={},
        serial_context={
            "transcript_path": "/tmp/serial.log",
            "reboot_cycles": 3,
            "serial_snippet": ["line1", "line2"],
        },
    )
    paths = write_evidence_bundle(bundle, str(tmp_path))
    text = Path(paths["summary_txt"]).read_text(encoding="utf-8")
    assert "/tmp/serial.log" in text
    assert "reboot cycles: 3" in text
    assert "line1" in text

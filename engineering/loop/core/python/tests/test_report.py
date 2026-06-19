"""loop_core/report.py 单元测试。"""
import json
import tempfile
from pathlib import Path

from loop_core.models import ActionRecord, LoopAttempt, RuleMatch
from loop_core.report import render_summary, write_report_bundle


def _make_attempt(**kwargs) -> LoopAttempt:
    defaults = dict(
        attempt_id="att-1",
        device_id="rp5",
        outcome="EXIT_FAILURE",
        final_classification="no_output_after_attach",
        boot_cycle_count=0,
    )
    defaults.update(kwargs)
    return LoopAttempt(**defaults)


class TestRenderSummary:
    def test_contains_classification(self):
        summary = render_summary(_make_attempt())
        assert "no_output_after_attach" in summary

    def test_contains_outcome(self):
        summary = render_summary(_make_attempt())
        assert "EXIT_FAILURE" in summary

    def test_contains_cycle_count(self):
        attempt = _make_attempt(boot_cycle_count=3)
        summary = render_summary(attempt)
        assert "cycle_count: 3" in summary

    def test_contains_matched_rules(self):
        attempt = _make_attempt(
            matched_rules=[
                RuleMatch("rule_a", True, 0.9, "high", ["evidence1"], "P", [])
            ]
        )
        summary = render_summary(attempt)
        assert "rule_a" in summary

    def test_contains_actions(self):
        attempt = _make_attempt(
            actions=[
                ActionRecord("a-1", "L1", "dmesg", "reason", "OK"),
            ]
        )
        summary = render_summary(attempt)
        assert "dmesg" in summary

    def test_l1_output_preview(self):
        attempt = _make_attempt(
            actions=[
                ActionRecord(
                    "a-1", "L1", "dmesg", "reason", "OK",
                    output_lines=["[ 1.0 ] init", "[ 2.0 ] service"],
                )
            ]
        )
        summary = render_summary(attempt)
        assert "L1采样" in summary
        assert "dmesg" in summary
        assert "init" in summary

    def test_no_l1_preview_when_empty(self):
        attempt = _make_attempt()
        summary = render_summary(attempt)
        assert "L1采样" not in summary

    def test_extra_summary_lines_appended(self):
        attempt = _make_attempt(extra_summary_lines=["boot_cycle: 1"])
        summary = render_summary(attempt)
        assert "boot_cycle: 1" in summary

    def test_advice_map_injected(self):
        attempt = _make_attempt(final_classification="kernel_panic_detected")
        summary = render_summary(
            attempt,
            advice_map={"kernel_panic_detected": "检查 kernel panic 日志"},
        )
        assert "检查 kernel panic 日志" in summary

    def test_advice_map_default_success(self):
        attempt = _make_attempt(
            outcome="EXIT_SUCCESS",
            final_classification="shell_prompt_available",
        )
        summary = render_summary(attempt)
        assert "正常启动" in summary


class TestWriteReportBundle:
    def test_creates_report_json(self):
        attempt = _make_attempt()
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(attempt, tmpdir)
            assert "report_json" in paths
            assert Path(paths["report_json"]).exists()
            data = json.loads(Path(paths["report_json"]).read_text())
            assert data["attempt_id"] == "att-1"

    def test_creates_summary_txt(self):
        attempt = _make_attempt()
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(attempt, tmpdir)
            assert "summary_txt" in paths
            txt = Path(paths["summary_txt"]).read_text()
            assert "no_output_after_attach" in txt

    def test_creates_captured_lines_txt(self):
        attempt = _make_attempt()
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(
                attempt, tmpdir, snapshot_lines=["line1", "line2"]
            )
            assert "captured_lines_txt" in paths
            txt = Path(paths["captured_lines_txt"]).read_text()
            assert "line1" in txt

    def test_no_captured_lines_when_not_provided(self):
        attempt = _make_attempt()
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(attempt, tmpdir)
            assert "captured_lines_txt" not in paths

    def test_advice_map_passed_to_render(self):
        attempt = _make_attempt(final_classification="kernel_panic_detected")
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(
                attempt,
                tmpdir,
                advice_map={"kernel_panic_detected": "检查 panic"},
            )
            txt = Path(paths["summary_txt"]).read_text()
            assert "检查 panic" in txt

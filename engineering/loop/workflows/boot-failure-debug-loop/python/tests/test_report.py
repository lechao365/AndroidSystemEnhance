"""report.py 与 cli.py 单元测试。

覆盖：
- render_summary 生成人类可读摘要
- write_report_bundle 生成 JSON + TXT + captured_lines
- CLI fixture mode 与 live mode（通过 mock transport）
"""
from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import subprocess
import sys
import tempfile

import pytest

from loop_core.models import LoopAttempt, RuleMatch, ActionRecord
from loop_core.report import render_summary, write_report_bundle
from boot_failure_debug.runner import BootFailureRunner
from loop_core.transport import FixtureTransport
from boot_failure_debug.config import load_profiles

REPO = Path(__file__).resolve().parents[6]
DEVICE_PROFILE = REPO / "engineering/loop/connection/profiles/devices/rp5/default.json"
WORKFLOW_PROFILE = REPO / "engineering/loop/profiles/boot-failure-debug/default.json"
FIXTURES = REPO / "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures"


def _cfg():
    return load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE))


# ============================================================================
# render_summary
# ============================================================================

class TestRenderSummary:
    def test_contains_classification(self):
        attempt = LoopAttempt(
            attempt_id="att-1",
            device_id="rp5",
            outcome="EXIT_FAILURE",
            final_classification="kernel_panic_detected",
            boot_cycle_count=1,
            matched_rules=[
                RuleMatch(
                    rule_id="kernel_panic_detected",
                    matched=True,
                    confidence=0.95,
                    severity="high",
                    evidence=["Kernel panic"],
                    phase="CLASSIFY_FAILURE",
                    suggested_actions=["capture_recent_context"],
                )
            ],
            actions=[
                ActionRecord(
                    action_id="a-1",
                    level="L2",
                    command="capture_recent_context",
                    reason="panic detected",
                    result="OK",
                )
            ],
        )
        summary = render_summary(attempt)
        assert "kernel_panic_detected" in summary
        assert "EXIT_FAILURE" in summary

    def test_contains_boot_cycle_count(self):
        attempt = LoopAttempt(
            attempt_id="att-2",
            device_id="rp5",
            outcome="EXIT_FAILURE",
            final_classification="reboot_loop_detected",
            boot_cycle_count=3,
            matched_rules=[],
            actions=[],
            extra_summary_lines=["boot_cycle: 3"],
        )
        summary = render_summary(attempt)
        assert "boot_cycle: 3" in summary

    def test_contains_matched_rules(self):
        attempt = LoopAttempt(
            attempt_id="att-3",
            device_id="rp5",
            outcome="EXIT_SUCCESS",
            final_classification="shell_prompt_available",
            boot_cycle_count=1,
            matched_rules=[
                RuleMatch(
                    rule_id="shell_prompt_available",
                    matched=True,
                    confidence=0.9,
                    severity="low",
                    evidence=["console:/ $"],
                    phase="CLASSIFY_FAILURE",
                    suggested_actions=["collect_read_only"],
                )
            ],
            actions=[
                ActionRecord(
                    action_id="a-1",
                    level="L1",
                    command="dmesg",
                    reason="prompt available",
                    result="OK",
                )
            ],
        )
        summary = render_summary(attempt)
        assert "shell_prompt_available" in summary

    def test_contains_actions(self):
        attempt = LoopAttempt(
            attempt_id="att-4",
            device_id="rp5",
            outcome="EXIT_SUCCESS",
            final_classification="shell_prompt_available",
            boot_cycle_count=1,
            matched_rules=[],
            actions=[
                ActionRecord(
                    action_id="a-1",
                    level="L1",
                    command="dmesg",
                    reason="prompt available",
                    result="OK",
                ),
                ActionRecord(
                    action_id="a-2",
                    level="L1",
                    command="getprop",
                    reason="prompt available",
                    result="OK",
                ),
            ],
        )
        summary = render_summary(attempt)
        assert "dmesg" in summary

    def test_contains_l1_output_preview(self):
        attempt = LoopAttempt(
            attempt_id="att-report",
            device_id="rp5",
            outcome="EXIT_SUCCESS",
            final_classification="shell_prompt_available",
            boot_cycle_count=1,
            matched_rules=[],
            actions=[
                ActionRecord(
                    action_id="a-1",
                    level="L1",
                    command="dmesg",
                    reason="prompt available",
                    result="OK",
                    output_lines=["[ 1.0 ] init started", "[ 2.0 ] servicemanager ready"],
                    metadata={"captured_line_count": 2},
                )
            ],
        )
        summary = render_summary(attempt)
        assert "L1采样" in summary
        assert "dmesg" in summary
        assert "init started" in summary

    def test_omits_l1_output_preview_when_no_output_lines(self):
        attempt = LoopAttempt(
            attempt_id="att-report-empty",
            device_id="rp5",
            outcome="EXIT_SUCCESS",
            final_classification="shell_prompt_available",
            boot_cycle_count=1,
            matched_rules=[],
            actions=[
                ActionRecord(
                    action_id="a-1",
                    level="L1",
                    command="dmesg",
                    reason="prompt available",
                    result="OK",
                    output_lines=[],
                    metadata={"captured_line_count": 0},
                )
            ],
        )
        summary = render_summary(attempt)
        assert "L1采样" not in summary


# ============================================================================
# write_report_bundle
# ============================================================================

class TestWriteReportBundle:
    def test_creates_report_json(self):
        attempt = LoopAttempt(
            attempt_id="att-5",
            device_id="rp5",
            outcome="EXIT_FAILURE",
            final_classification="no_output_after_attach",
            boot_cycle_count=0,
            matched_rules=[],
            actions=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(attempt, tmpdir)
            assert "report_json" in paths
            assert Path(paths["report_json"]).exists()
            data = json.loads(Path(paths["report_json"]).read_text())
            assert data["attempt_id"] == "att-5"
            assert data["final_classification"] == "no_output_after_attach"

    def test_creates_summary_txt(self):
        attempt = LoopAttempt(
            attempt_id="att-6",
            device_id="rp5",
            outcome="EXIT_SUCCESS",
            final_classification="shell_prompt_available",
            boot_cycle_count=1,
            matched_rules=[],
            actions=[],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(attempt, tmpdir)
            assert "summary_txt" in paths
            txt = Path(paths["summary_txt"]).read_text()
            assert "shell_prompt_available" in txt

    def test_creates_captured_lines_txt(self):
        # 需要 snapshot，runner.run() 后才有
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "normal_boot.jsonl"))
        runner = BootFailureRunner(cfg, transport)
        attempt = runner.run()
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_report_bundle(attempt, tmpdir, snapshot_lines=["line1", "line2"])
            assert "captured_lines_txt" in paths
            txt = Path(paths["captured_lines_txt"]).read_text()
            assert "line1" in txt


# ============================================================================
# CLI
# ============================================================================

class TestCLI:
    def test_joint_collection_allows_importing_workflow_test_modules(self):
        script = """
import importlib
import sys
sys.path = [
    'engineering/loop/connection/providers/rp5-serial/python',
    'engineering/loop/workflows/boot-failure-debug-loop/python',
] + sys.path
sys.modules.pop('tests', None)
module = importlib.import_module('tests.test_report')
print(module.__file__)
""".strip()
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip().endswith(
            "engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py"
        )

    def test_cli_fixture_mode_generates_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from boot_failure_debug.cli import main
            rc = main([
                "--fixture", str(FIXTURES / "normal_boot.jsonl"),
                "--device-profile", str(DEVICE_PROFILE),
                "--workflow-profile", str(WORKFLOW_PROFILE),
                "--artifacts-dir", tmpdir,
            ])
            assert rc == 0
            assert Path(tmpdir, "report.json").exists()
            assert Path(tmpdir, "summary.txt").exists()

    def test_cli_fixture_kernel_panic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from boot_failure_debug.cli import main
            rc = main([
                "--fixture", str(FIXTURES / "kernel_panic.jsonl"),
                "--device-profile", str(DEVICE_PROFILE),
                "--workflow-profile", str(WORKFLOW_PROFILE),
                "--artifacts-dir", tmpdir,
            ])
            assert rc == 0
            data = json.loads(Path(tmpdir, "report.json").read_text())
            assert data["outcome"] == "EXIT_FAILURE"
            assert data["final_classification"] == "kernel_panic_detected"
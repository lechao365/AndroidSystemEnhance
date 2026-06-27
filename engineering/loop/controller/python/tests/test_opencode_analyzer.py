"""OpencodeAnalyzer 单元测试：通过 subprocess 调 opencode run 生成补丁。"""
import json
import subprocess
from unittest.mock import MagicMock, patch

from loop_controller.analyzer_protocol import AnalysisRequest, OpencodeAnalyzer


def _mock_opencode_output(patches: list[dict]) -> str:
    return json.dumps([
        {"type": "assistant", "content": json.dumps(patches)}
    ])


def test_opencode_analyzer_parses_valid_output(tmp_path):
    patches = [{"workspace_path": "foo.c", "change_type": "edit",
                "old_marker": "x", "new_content": "y"}]
    mock_output = _mock_opencode_output(patches)
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=10)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "foo.c"


def test_opencode_analyzer_timeout_returns_empty(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=1)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []
    assert suggestion.confidence == 0.0


def test_opencode_analyzer_nonzero_exit_returns_empty(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []


def test_opencode_analyzer_no_json_in_output_returns_empty(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="no json here")
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []


def test_opencode_prompt_includes_evidence_and_failed_cases(tmp_path):
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    req = AnalysisRequest(
        session_id="s", attempt_index=1, target="lciod",
        failed_cases=[{"id": "HA-03", "failure_reason": "field mismatch"}],
        evidence_bundle_path="/tmp/eb.json",
    )
    prompt = analyzer._build_prompt(req)
    assert "HA-03" in prompt
    assert "field mismatch" in prompt
    assert "/tmp/eb.json" in prompt

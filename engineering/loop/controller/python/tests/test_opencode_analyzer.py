"""OpencodeAnalyzer 单元测试：通过 subprocess 调 opencode run 生成补丁。"""
import json
import subprocess
from unittest.mock import MagicMock, patch

from loop_controller.analyzer_protocol import AnalysisRequest, OpencodeAnalyzer


def _mock_opencode_output(text_content: str) -> str:
    """构造 opencode run --format json 的 JSONL 流式输出。

    text_content 是 LLM 输出的文本（可能含 JSON 数组、markdown 围栏等）。
    """
    return "\n".join([
        json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
        json.dumps({"type": "text", "part": {"type": "text", "text": text_content}}),
        json.dumps({"type": "step_finish", "part": {"type": "step-finish"}}),
    ])


def test_opencode_analyzer_parses_valid_output(tmp_path):
    patches = [{"workspace_path": "foo.c", "change_type": "edit",
                "old_marker": "x", "new_content": "y"}]
    mock_output = _mock_opencode_output(json.dumps(patches))
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=10)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "foo.c"


def test_opencode_analyzer_parses_markdown_fenced_output(tmp_path):
    """LLM 用 ```json 围栏包裹 JSON 数组时也能正确提取。"""
    patches = [{"workspace_path": "bar.c", "change_type": "edit",
                "old_marker": "a", "new_content": "b"}]
    fenced = "```json\n" + json.dumps(patches) + "\n```"
    mock_output = _mock_opencode_output(fenced)
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=10)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "bar.c"


def test_opencode_analyzer_concatenates_multi_text_events(tmp_path):
    """多个 text 事件片段拼接为完整文本后再提取 JSON。"""
    patches = [{"workspace_path": "baz.c", "change_type": "edit",
                "old_marker": "p", "new_content": "q"}]
    json_str = json.dumps(patches)
    mock_output = "\n".join([
        json.dumps({"type": "text", "part": {"type": "text", "text": json_str[:5]}}),
        json.dumps({"type": "text", "part": {"type": "text", "text": json_str[5:]}}),
    ])
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=10)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)
        req = AnalysisRequest(session_id="s", attempt_index=1)
        suggestion = analyzer.analyze(req)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "baz.c"


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


def test_opencode_invoke_cmd_uses_separator(tmp_path):
    """opencode -f 是 array 参数会吞掉后续 positionals，
    必须用 -- 分隔文件参数和 message，否则 message 被当作文件路径。"""
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path), timeout=10)
    req = AnalysisRequest(session_id="s", attempt_index=1)
    prompt = analyzer._build_prompt(req)
    req_file = analyzer._write_request_file(req)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        analyzer._invoke_opencode(prompt, req_file)
    cmd = mock_run.call_args[0][0]
    assert "--" in cmd
    sep_idx = cmd.index("--")
    assert cmd[sep_idx - 1] == req_file or req_file in cmd[sep_idx - 2:sep_idx]
    assert prompt in cmd[sep_idx + 1:]


def test_opencode_prompt_includes_history_when_prior_attempts_exist(tmp_path):
    """G3: prior_attempts 非空时 prompt 含'历史尝试'段落。"""
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    req = AnalysisRequest(
        session_id="s", attempt_index=2, target="lciod",
        failed_cases=[{"id": "TC-02", "failure_reason": "still failing"}],
        prior_attempts=[
            {
                "attempt_index": 0,
                "patch_hash": "abc123",
                "failure_code": "COMPILE_FAILED",
                "failed_count": 2,
                "patch_files": ["vendor/lechao/foo.c", "vendor/lechao/bar.h"],
                "failure_summary": "implicit declaration of function 'bar'",
            },
        ],
    )
    prompt = analyzer._build_prompt(req)
    assert "历史尝试" in prompt
    assert "abc123" not in prompt  # hash 本身不应直接渲染（太长无意义）
    assert "foo.c" in prompt
    assert "COMPILE_FAILED" in prompt
    assert "implicit declaration" in prompt


def test_opencode_prompt_no_history_section_when_empty(tmp_path):
    """G3: prior_attempts 为空时不渲染历史段落。"""
    analyzer = OpencodeAnalyzer(workspace_root=str(tmp_path))
    req = AnalysisRequest(session_id="s", attempt_index=1, target="lciod")
    prompt = analyzer._build_prompt(req)
    assert "历史尝试" not in prompt

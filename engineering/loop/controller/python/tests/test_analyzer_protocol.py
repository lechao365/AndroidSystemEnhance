"""analyzer_protocol.ScriptedAnalyzer 单元测试。"""
import json
from pathlib import Path

from loop_controller.analyzer_protocol import (
    AnalysisRequest,
    FileChange,
    LlmAnalyzer,
    PatchSuggestion,
    ScriptedAnalyzer,
    _rule_lciod_daemon_formula_error,
    _rule_lciod_hal_field_inversion,
    _rule_lciod_hal_readdrain_missing,
)


def test_scripted_analyzer_is_llm_analyzer_subclass():
    analyzer = ScriptedAnalyzer()
    assert isinstance(analyzer, LlmAnalyzer)


def test_analyze_returns_patch_suggestion():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert isinstance(result, PatchSuggestion)


def test_analyze_returns_empty_target_files_when_no_rule_matches():
    """无规则匹配时返回空补丁（与确定性规则并存）。"""
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert result.target_files == []


def test_analyze_rationale_indicates_human_intervention_when_no_match():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert "人工" in result.rationale or "AI" in result.rationale


def test_analyze_confidence_is_zero_when_no_match():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert result.confidence == 0.0


def test_analyze_empty_failed_cases_returns_empty_patch():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1, failed_cases=[])
    result = analyzer.analyze(request)
    assert result.target_files == []


# ---------------------------------------------------------------------------
# FV_STDOUT_POLLUTION 规则：fault-verify stdout 污染 JSON 输出
# ---------------------------------------------------------------------------

def _write_pollution_bundle(bundle_path: Path) -> None:
    """写入 stdout 污染失败指纹的 evidence_bundle.json。"""
    bundle = {
        "summary": {"overall": "FAIL", "total": 1, "passed": 0, "failed": 1, "skipped": 0},
        "cases": [{
            "id": "features.lciod.end_to_end.e2e_stats_reset_and_check",
            "status": "fail",
            "failure_reason": "output is not valid JSON: Expecting value: line 1 column 1 (char 0)",
            "command": "fault-verify stats reset; sleep 1; fault-verify stats get --json",
            "assertion": {"type": "json_field", "path": "write_bytes", "op": "gt", "value": 0},
            "output": "State reset OK\n{\n  \"read_bytes\": 0,\n  \"write_bytes\": 1049600\n}",
        }],
    }
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")


def test_fv_stdout_pollution_produces_patch(tmp_path: Path):
    """失败指纹匹配 FV_STDOUT_POLLUTION 时，产出修复 main.c printf 的 FileChange。"""
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_pollution_bundle(bundle_path)

    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(
        session_id="s1", attempt_index=1,
        failed_cases=[{
            "id": "features.lciod.end_to_end.e2e_stats_reset_and_check",
            "status": "fail",
            "failure_reason": "output is not valid JSON",
            "command": "fault-verify stats reset; sleep 1; fault-verify stats get --json",
        }],
        evidence_bundle_path=str(bundle_path),
    )
    result = analyzer.analyze(request)
    assert len(result.target_files) > 0
    assert result.confidence > 0.0
    # 修复目标必须是 main.c
    paths = [fc.workspace_path for fc in result.target_files]
    assert any("main.c" in p for p in paths), f"expected main.c in {paths}"


def test_fv_stdout_pollution_fix_changes_printf_to_fprintf(tmp_path: Path):
    """产出的 FileChange 必须把 printf 改为 fprintf(stderr, ...)。"""
    bundle_path = tmp_path / "evidence_bundle.json"
    _write_pollution_bundle(bundle_path)

    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(
        session_id="s1", attempt_index=1,
        failed_cases=[{
            "id": "features.lciod.end_to_end.e2e_stats_reset_and_check",
            "status": "fail",
            "failure_reason": "output is not valid JSON",
            "command": "fault-verify stats reset; fault-verify stats get --json",
        }],
        evidence_bundle_path=str(bundle_path),
    )
    result = analyzer.analyze(request)
    for fc in result.target_files:
        assert "fprintf(stderr" in fc.new_content, f"new_content should use fprintf(stderr): {fc.new_content}"
        assert "printf(" in fc.old_marker, f"old_marker should contain printf(: {fc.old_marker}"


def test_fv_stdout_pollution_does_not_trigger_on_unrelated_failure(tmp_path: Path):
    """不相关失败（非 JSON 解析错误）不应触发该规则。"""
    bundle = {
        "summary": {"overall": "FAIL", "total": 1, "passed": 0, "failed": 1, "skipped": 0},
        "cases": [{
            "id": "some.other.case",
            "status": "fail",
            "failure_reason": "expected output to contain FOO, got BAR",
            "command": "echo test",
            "assertion": {"type": "contains", "value": "FOO"},
            "output": "BAR",
        }],
    }
    bundle_path = tmp_path / "evidence_bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(
        session_id="s1", attempt_index=1,
        failed_cases=[{
            "id": "some.other.case",
            "status": "fail",
            "failure_reason": "expected output to contain FOO, got BAR",
            "command": "echo test",
        }],
        evidence_bundle_path=str(bundle_path),
    )
    result = analyzer.analyze(request)
    assert result.target_files == []
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# LCIOD HAL 字段反转：read_bytes 和 write_bytes 互换
# ---------------------------------------------------------------------------

def test_hal_field_inversion_detects_swapped_bytes():
    case = {
        "id": "HA-05",
        "failure_reason": "json_field read_bytes expected>0 but got 0, "
                          "write_bytes mismatch: expected 1024 got 2048",
        "command": "fault-verify stats --json",
    }
    changes = _rule_lciod_hal_field_inversion(case)
    assert changes is not None
    assert len(changes) > 0


# ---------------------------------------------------------------------------
# LCIOD Daemon getAverageRate 公式错误
# ---------------------------------------------------------------------------

def test_daemon_formula_error_detects_negative_rate():
    case = {
        "id": "DA-07",
        "failure_reason": "json_field getAverageRate expected ge 0 but got -1.5",
        "command": "fault-verify stats --json",
    }
    changes = _rule_lciod_daemon_formula_error(case)
    assert changes is not None


# ---------------------------------------------------------------------------
# LCIOD HAL readEvent 排空遗漏
# ---------------------------------------------------------------------------

def test_hal_readdrain_missing_detects_incomplete_events():
    case = {
        "id": "HA-09",
        "failure_reason": "readEvent returned 0 events after dd write, expected > 0",
        "command": "fault-verify event --read --count 5 --json",
    }
    changes = _rule_lciod_hal_readdrain_missing(case)
    assert changes is not None


# ---------------------------------------------------------------------------
# LCIOD 规则不应对无关失败产生误报
# ---------------------------------------------------------------------------

def test_lciod_rules_no_false_positive_on_unrelated_failure():
    case = {
        "id": "HA-01",
        "failure_reason": "service vendor.lechao.lciod not found",
        "command": "adb shell service list",
    }
    assert _rule_lciod_hal_field_inversion(case) is None
    assert _rule_lciod_daemon_formula_error(case) is None
    assert _rule_lciod_hal_readdrain_missing(case) is None


def test_analysis_request_prior_attempts_default_empty():
    """G3: AnalysisRequest 新增 prior_attempts 字段，默认空列表。"""
    req = AnalysisRequest(session_id="s", attempt_index=0)
    assert req.prior_attempts == []


def test_analysis_request_prior_attempts_accepts_list():
    """G3: prior_attempts 接受列表值。"""
    req = AnalysisRequest(
        session_id="s", attempt_index=0,
        prior_attempts=[{"attempt_index": 0, "patch_hash": "abc123"}],
    )
    assert len(req.prior_attempts) == 1
    assert req.prior_attempts[0]["patch_hash"] == "abc123"

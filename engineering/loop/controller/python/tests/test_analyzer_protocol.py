"""analyzer_protocol.ScriptedAnalyzer 单元测试。"""
from loop_controller.analyzer_protocol import (
    AnalysisRequest,
    LlmAnalyzer,
    PatchSuggestion,
    ScriptedAnalyzer,
)


def test_scripted_analyzer_is_llm_analyzer_subclass():
    analyzer = ScriptedAnalyzer()
    assert isinstance(analyzer, LlmAnalyzer)


def test_analyze_returns_patch_suggestion():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert isinstance(result, PatchSuggestion)


def test_analyze_returns_empty_target_files():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert result.target_files == []


def test_analyze_rationale_indicates_human_intervention():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert "人工" in result.rationale or "AI" in result.rationale


def test_analyze_confidence_is_zero():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1)
    result = analyzer.analyze(request)
    assert result.confidence == 0.0


def test_analyze_empty_failed_cases_returns_empty_patch():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(session_id="s1", attempt_index=1, failed_cases=[])
    result = analyzer.analyze(request)
    assert result.target_files == []
    assert result.confidence == 0.0


def test_analyze_non_empty_failed_cases_still_returns_empty_patch():
    analyzer = ScriptedAnalyzer()
    request = AnalysisRequest(
        session_id="s1",
        attempt_index=2,
        failed_cases=[{"name": "test_foo", "failure": "segfault"}],
    )
    result = analyzer.analyze(request)
    assert result.target_files == []
    assert result.confidence == 0.0

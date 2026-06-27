"""知识库与 fingerprint 计算单元测试。"""
import json
import re

from loop_controller.analyzer_protocol import (
    AnalysisRequest,
    FileChange,
    KBEntry,
    KnowledgeBaseAnalyzer,
    _compute_fingerprint,
)


def test_fingerprint_stable_for_same_input():
    req = AnalysisRequest(
        session_id="s1", attempt_index=1, target="lciod",
        suite="features.lciod.end_to_end",
        failed_cases=[
            {"id": "HA-03", "failure_reason": "getStats field mismatch: read_bytes wrong"},
            {"id": "HA-07", "failure_reason": "readEvent incomplete"},
        ],
    )
    fp1 = _compute_fingerprint(req)
    fp2 = _compute_fingerprint(req)
    assert fp1 == fp2
    assert fp1.startswith("sha256:")


def test_fingerprint_differs_for_different_cases():
    req_a = AnalysisRequest(session_id="s1", attempt_index=1, target="lciod",
        suite="s", failed_cases=[{"id": "HA-03", "failure_reason": "x"}])
    req_b = AnalysisRequest(session_id="s1", attempt_index=1, target="lciod",
        suite="s", failed_cases=[{"id": "HA-07", "failure_reason": "y"}])
    assert _compute_fingerprint(req_a) != _compute_fingerprint(req_b)


def test_fingerprint_normalizes_path_and_whitespace():
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "error in /vendor/lechao/foo.cpp at line 10"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "error  in  <path>  at  line  10"}])
    assert _compute_fingerprint(req1) == _compute_fingerprint(req2)


def test_fingerprint_case_insensitive():
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "Field Mismatch"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "field mismatch"}])
    assert _compute_fingerprint(req1) == _compute_fingerprint(req2)


def test_fingerprint_order_independent():
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "B", "failure_reason": "x"}, {"id": "A", "failure_reason": "y"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "A", "failure_reason": "y"}, {"id": "B", "failure_reason": "x"}])
    assert _compute_fingerprint(req1) == _compute_fingerprint(req2)


# ---------------------------------------------------------------------------
# KnowledgeBaseAnalyzer
# ---------------------------------------------------------------------------
def _make_kb_file(tmp_path, entries):
    kb = tmp_path / "kb.json"
    kb.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return str(kb)


def test_kb_analyzer_loads_from_file(tmp_path):
    entry = {
        "fingerprint": "sha256:abc",
        "patch": [{"workspace_path": "foo.c", "change_type": "edit",
                    "old_marker": "x", "new_content": "y"}],
        "description": "test entry",
        "deploy_mode_hint": "PUSH_SINGLE",
    }
    kb_path = _make_kb_file(tmp_path, [entry])
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    assert len(analyzer._kb) == 1


def test_kb_analyzer_hit_returns_patch(tmp_path):
    entry = {
        "fingerprint": "sha256:abc",
        "patch": [{"workspace_path": "foo.c", "change_type": "edit",
                    "old_marker": "x", "new_content": "y"}],
        "description": "test",
    }
    kb_path = _make_kb_file(tmp_path, [entry])
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    analyzer._compute_fingerprint = lambda r: "sha256:abc"
    req = AnalysisRequest(session_id="s", attempt_index=1)
    suggestion = analyzer.analyze(req)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "foo.c"
    assert suggestion.confidence == 0.98


def test_kb_analyzer_miss_returns_empty(tmp_path):
    kb_path = _make_kb_file(tmp_path, [])
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    req = AnalysisRequest(session_id="s", attempt_index=1,
                          failed_cases=[{"id": "C1", "failure_reason": "x"}])
    suggestion = analyzer.analyze(req)
    assert suggestion.target_files == []
    assert suggestion.confidence == 0.0


def test_kb_analyzer_missing_file_returns_empty_list(tmp_path):
    analyzer = KnowledgeBaseAnalyzer(str(tmp_path / "nonexistent.json"))
    assert analyzer._kb == []


def test_kb_analyzer_corrupt_json_returns_empty_list(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    analyzer = KnowledgeBaseAnalyzer(str(bad))
    assert analyzer._kb == []

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


def test_fingerprint_normalizes_dynamic_numbers():
    """动态数值（如 logcat 计数 got: 1 / got: 2）应归一化为同一指纹。"""
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="lcview",
        suite="features.lcview.common",
        failed_cases=[{"id": "lcview_no_validate_errors",
                       "failure_reason": "expected output to contain '0', got: 1"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="lcview",
        suite="features.lcview.common",
        failed_cases=[{"id": "lcview_no_validate_errors",
                       "failure_reason": "expected output to contain '0', got: 2"}])
    assert _compute_fingerprint(req1) == _compute_fingerprint(req2)


def test_fingerprint_normalizes_hex_and_counts():
    """十六进制地址、大整数计数应归一化。"""
    req1 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "mismatch at 0x7fff100 count=12345"}])
    req2 = AnalysisRequest(session_id="s1", attempt_index=1, target="t", suite="s",
        failed_cases=[{"id": "C1", "failure_reason": "mismatch at 0xabcd200 count=67890"}])
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


def test_hit_count_increments_on_match(tmp_path):
    """KB 命中时 hit_count 递增并写回文件。"""
    req = AnalysisRequest(
        session_id="s", attempt_index=1, target="lciod",
        suite="features.lciod.end_to_end",
        failed_cases=[{"id": "HA-03", "failure_reason": "getStats field mismatch"}],
    )
    fp = _compute_fingerprint(req)
    entry = {
        "fingerprint": fp,
        "patch": [{"workspace_path": "foo.c", "change_type": "edit",
                    "old_marker": "x", "new_content": "y"}],
        "description": "test",
        "hit_count": 0,
    }
    kb_path = _make_kb_file(tmp_path, [entry])

    # 第一次命中
    analyzer1 = KnowledgeBaseAnalyzer(kb_path)
    suggestion1 = analyzer1.analyze(req)
    assert len(suggestion1.target_files) == 1

    # 重新加载 KB，验证 hit_count 已递增
    analyzer2 = KnowledgeBaseAnalyzer(kb_path)
    assert analyzer2._kb[0].hit_count == 1
    assert analyzer2._kb[0].last_hit_at != ""

    # 第二次命中
    suggestion2 = analyzer2.analyze(req)
    assert len(suggestion2.target_files) == 1
    analyzer3 = KnowledgeBaseAnalyzer(kb_path)
    assert analyzer3._kb[0].hit_count == 2

"""知识库与 fingerprint 计算单元测试。"""
import re

from loop_controller.analyzer_protocol import AnalysisRequest, _compute_fingerprint


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

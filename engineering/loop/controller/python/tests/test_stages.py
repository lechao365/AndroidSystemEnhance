import json
import subprocess
from pathlib import Path

from loop_controller.stages import (
    StageContext,
    analyze_request_stage,
    decide_stage,
    run_verify_stage,
)


def test_stage_context_dataclass():
    ctx = StageContext(
        cases_dir="/tmp/cases", device_profile="rp5",
        artifacts_dir="/tmp/artifacts", session_id="s1",
    )
    assert ctx.cases_dir == "/tmp/cases"
    assert ctx.device_profile == "rp5"
    assert ctx.artifacts_dir == "/tmp/artifacts"
    assert ctx.session_id == "s1"


def test_stage_context_defaults():
    ctx = StageContext()
    assert ctx.cases_dir == ""
    assert ctx.device_profile == ""


def test_run_verify_stage_reads_evidence_bundle(tmp_path: Path, monkeypatch):
    bundle = {
        "summary": {"overall": "FAIL", "total": 1, "passed": 0, "failed": 1, "skipped": 0},
        "cases": [{"id": "case.fail", "status": "fail", "failure_reason": "boom", "command": "echo boom"}],
    }
    session = {
        "session_id": "sess-001",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 0,
        "max_attempts": 5,
        "attempts": [],
        "status": "PENDING",
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        (tmp_path / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

        class R:
            returncode = 1
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    updated, stage = run_verify_stage(str(tmp_path / "session.json"), "test.yaml", "")
    assert updated["current_attempt"] == 1
    assert updated["attempts"][-1]["failed_cases"][0]["id"] == "case.fail"
    assert stage.failure_code.value == "RUN_FAILED"


def test_decide_stage_detects_duplicate_patch():
    session = {
        "current_attempt": 2,
        "max_attempts": 5,
        "status": "FAIL",
        "attempts": [
            {"attempt_index": 1, "failure_code": "RUN_FAILED", "patch_applied": {"patch_hash": "aaa"}},
            {"attempt_index": 2, "failure_code": "COMPILE_FAILED", "patch_applied": {"patch_hash": "aaa"}},
        ],
    }
    decision = decide_stage(session)
    assert decision["decision"] == "STOP"
    assert decision["reason"] == "duplicate_patch_detected"


def test_analyze_request_stage_writes_json(tmp_path: Path):
    session = {
        "session_id": "sess-001",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 1,
        "attempts": [{
            "attempt_index": 1,
            "failed_cases": [{"id": "case.fail", "status": "fail", "failure_reason": "boom", "command": "echo boom"}],
            "evidence_path": str(tmp_path / "evidence_bundle.json"),
        }],
    }
    (tmp_path / "evidence_bundle.json").write_text(
        json.dumps({"evidence": {"dmesg": {"commands": ["dmesg"]}}}), encoding="utf-8"
    )
    request_path = analyze_request_stage(session)
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    assert request["failed_cases"][0]["id"] == "case.fail"
    assert "dmesg" in request["collectors_output"]


def test_run_verify_stage_pass_case(tmp_path: Path, monkeypatch):
    """verify PASS 时 StageResult.failure_code 应为 NONE。"""
    session = {
        "session_id": "sess-ok",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 0,
        "max_attempts": 5,
        "attempts": [],
        "status": "PENDING",
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        (tmp_path / "evidence_bundle.json").write_text(
            json.dumps({"summary": {"overall": "PASS"}, "cases": []}), encoding="utf-8"
        )

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    updated, stage = run_verify_stage(str(tmp_path / "session.json"), "test.yaml", "")
    assert stage.status == "PASS"
    assert stage.failure_code.value == "NONE"
    assert updated["attempts"][-1]["failure_code"] == ""


def test_decide_stage_pass():
    """PASS 场景 decision=STOP, verification_passed。"""
    session = {
        "current_attempt": 1,
        "max_attempts": 5,
        "status": "PASS",
        "attempts": [{"attempt_index": 1, "failure_code": "", "verify_result": "PASS"}],
    }
    decision = decide_stage(session)
    assert decision["decision"] == "STOP"
    assert decision["should_escalate"] is False


def test_decide_stage_retry_on_first_fail():
    """首次失败、不同 failure_code 应 RETRY。"""
    session = {
        "current_attempt": 1,
        "max_attempts": 5,
        "status": "FAIL",
        "attempts": [{"attempt_index": 1, "failure_code": "RUN_FAILED"}],
    }
    decision = decide_stage(session)
    assert decision["decision"] == "RETRY"
    assert decision["should_escalate"] is False


def test_decide_stage_max_attempts_escalate():
    """超过 max_attempts 应 STOP + escalate。"""
    session = {
        "current_attempt": 6,
        "max_attempts": 5,
        "status": "FAIL",
        "attempts": [
            {"attempt_index": i, "failure_code": "RUN_FAILED" if i % 2 else "COMPILE_FAILED"}
            for i in range(1, 7)
        ],
    }
    decision = decide_stage(session)
    assert decision["decision"] == "STOP"
    assert decision["should_escalate"] is True


def test_run_verify_stage_records_case_results_and_failed_count(tmp_path: Path, monkeypatch):
    """run_verify_stage 应在 attempt 中记录逐用例结果（case_results）与失败用例数（failed_count），供收敛判定。"""
    bundle = {
        "summary": {"overall": "FAIL", "total": 5, "passed": 3, "failed": 2, "skipped": 0},
        "cases": [
            {"id": "a.pass", "status": "pass", "failure_reason": "", "command": "echo a"},
            {"id": "b.pass", "status": "pass", "failure_reason": "", "command": "echo b"},
            {"id": "c.pass", "status": "pass", "failure_reason": "", "command": "echo c"},
            {"id": "d.fail", "status": "fail", "failure_reason": "boom", "command": "echo d"},
            {"id": "e.fail", "status": "fail", "failure_reason": "boom2", "command": "echo e"},
        ],
    }
    session = {
        "session_id": "sess-cv",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 0,
        "max_attempts": 5,
        "attempts": [],
        "status": "PENDING",
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        (tmp_path / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

        class R:
            returncode = 1
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    updated, _ = run_verify_stage(str(tmp_path / "session.json"), "test.yaml", "")
    att = updated["attempts"][-1]
    assert att["failed_count"] == 2
    ids_in_results = {c["id"] for c in att["case_results"]}
    assert ids_in_results == {"a.pass", "b.pass", "c.pass", "d.fail", "e.fail"}
    statuses = {c["id"]: c["status"] for c in att["case_results"]}
    assert statuses["a.pass"] == "pass"
    assert statuses["d.fail"] == "fail"


def test_run_verify_stage_failed_count_zero_on_pass(tmp_path: Path, monkeypatch):
    """全 PASS 时 failed_count 应为 0。"""
    bundle = {
        "summary": {"overall": "PASS", "total": 2, "passed": 2, "failed": 0, "skipped": 0},
        "cases": [
            {"id": "x.pass", "status": "pass", "failure_reason": "", "command": "echo x"},
            {"id": "y.pass", "status": "pass", "failure_reason": "", "command": "echo y"},
        ],
    }
    session = {
        "session_id": "sess-ok2",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 0,
        "max_attempts": 5,
        "attempts": [],
        "status": "PENDING",
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        (tmp_path / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    updated, _ = run_verify_stage(str(tmp_path / "session.json"), "test.yaml", "")
    att = updated["attempts"][-1]
    assert att["failed_count"] == 0
    assert len(att["case_results"]) == 2


def test_run_verify_stage_handles_empty_cases(tmp_path: Path, monkeypatch):
    """无 cases 字段时 failed_count=0，case_results=[]。"""
    bundle = {"summary": {"overall": "PASS", "total": 0, "passed": 0, "failed": 0, "skipped": 0}, "cases": []}
    session = {
        "session_id": "sess-empty",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 0,
        "max_attempts": 5,
        "attempts": [],
        "status": "PENDING",
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        (tmp_path / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    updated, _ = run_verify_stage(str(tmp_path / "session.json"), "test.yaml", "")
    att = updated["attempts"][-1]
    assert att["failed_count"] == 0
    assert att["case_results"] == []


def test_analyze_request_stage_no_evidence_file(tmp_path: Path):
    """evidence_path 指向不存在的文件时不应抛异常。"""
    session = {
        "session_id": "sess-ne",
        "artifacts_dir": str(tmp_path),
        "current_attempt": 1,
        "attempts": [{
            "attempt_index": 1,
            "failed_cases": [{"id": "x", "status": "fail", "failure_reason": "", "command": ""}],
            "evidence_path": str(tmp_path / "missing.json"),
        }],
    }
    request_path = analyze_request_stage(session)
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    assert request["failed_cases"][0]["id"] == "x"
    assert request["collectors_output"] == {}

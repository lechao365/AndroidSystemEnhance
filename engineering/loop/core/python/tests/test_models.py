"""loop_core v2 数据模型测试。"""
from loop_core.models import (
    ObservedLine,
    TestCaseResult,
    CollectorResult,
    EvidenceBundle,
)


def test_observed_line_to_dict():
    line = ObservedLine(t=1.5, text="hello", cycle_id=2)
    assert line.to_dict() == {"t": 1.5, "text": "hello", "cycle_id": 2}


def test_test_case_result_defaults():
    r = TestCaseResult(id="zygote_running", suite="boot-success", status="pass")
    assert r.command == ""
    assert r.output == ""
    assert r.output_preview == ""
    assert r.assertion == {}
    assert r.duration_sec == 0.0
    assert r.failure_reason == ""
    assert r.skip_reason == ""
    assert r.triggered_collectors == []
    assert r.tags == []


def test_test_case_result_to_dict():
    r = TestCaseResult(
        id="zygote_running",
        suite="boot-success",
        status="fail",
        command="getprop init.svc.zygote",
        output="stopped\n",
        assertion={"type": "contains", "value": "running"},
        duration_sec=1.2,
        failure_reason="expected 'running', got 'stopped'",
        triggered_collectors=["crash_dump"],
    )
    d = r.to_dict()
    assert d["id"] == "zygote_running"
    assert d["status"] == "fail"
    assert d["triggered_collectors"] == ["crash_dump"]


def test_collector_result_to_dict():
    cr = CollectorResult(
        name="crash_dump",
        commands=["logcat -b crash -d", "ls -la /data/tombstones/"],
        outputs=[
            {"command": "logcat -b crash -d", "lines": ["crash line 1"]},
        ],
        hints="关注 abort message",
    )
    d = cr.to_dict()
    assert d["name"] == "crash_dump"
    assert len(d["commands"]) == 2
    assert d["hints"] == "关注 abort message"


def test_evidence_bundle_to_dict():
    bundle = EvidenceBundle(
        bundle_id="eb-test-001",
        device_id="rp5",
        suite="boot-success",
        timestamp="2026-06-19T22:36:06+08:00",
        summary={"total": 2, "passed": 1, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[
            TestCaseResult(id="shell_reachable", suite="boot-success", status="pass"),
            TestCaseResult(id="zygote_running", suite="boot-success", status="fail"),
        ],
        evidence={},
    )
    d = bundle.to_dict()
    assert d["bundle_id"] == "eb-test-001"
    assert d["summary"]["overall"] == "FAIL"
    assert len(d["cases"]) == 2


def test_reboot_result_pass():
    """RebootResult 成功场景。"""
    from loop_core.models import RebootResult

    result = RebootResult(
        status="pass",
        transcript_lines=["Booting Linux", "init: zygote"],
        failure_reason="",
        stage_reached="l3_verified",
        boot_duration_sec=42.5,
    )
    assert result.status == "pass"
    assert result.stage_reached == "l3_verified"
    assert result.boot_duration_sec == 42.5
    d = result.to_dict()
    assert d["status"] == "pass"


def test_reboot_result_fail_timeout():
    """RebootResult 超时失败场景。"""
    from loop_core.models import RebootResult

    result = RebootResult(
        status="fail",
        transcript_lines=["Booting Linux"],
        failure_reason="timeout",
        stage_reached="l2_init_ready",
        boot_duration_sec=90.0,
    )
    assert result.status == "fail"
    assert result.failure_reason == "timeout"

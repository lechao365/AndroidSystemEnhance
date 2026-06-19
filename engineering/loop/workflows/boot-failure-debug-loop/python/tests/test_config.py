"""profile 与 fixture 的合同测试。

确保 device/workflow profile 包含 V1 必需字段，且 5 个 fixture 文件齐全。
"""
from pathlib import Path
import json

REPO = Path(__file__).resolve().parents[6]

DEVICE_PROFILE = REPO / "engineering/loop/connection/profiles/devices/rp5/default.json"
WORKFLOW_PROFILE = REPO / "engineering/loop/profiles/boot-failure-debug/default.json"
FIXTURES_DIR = REPO / "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures"


def test_rp5_default_profile_contains_required_markers():
    profile = json.loads(DEVICE_PROFILE.read_text())
    assert profile["device_id"] == "rp5"
    assert profile["prompt_markers"], "prompt_markers 不能为空"
    assert profile["boot_markers"], "boot_markers 不能为空"
    assert profile["reboot_markers"], "reboot_markers 不能为空"
    assert profile["panic_markers"], "panic_markers 不能为空"
    assert profile["hang_markers"], "hang_markers 不能为空"


def test_boot_failure_workflow_profile_contains_v1_thresholds():
    profile = json.loads(WORKFLOW_PROFILE.read_text())
    assert profile["observe_timeout_sec"] > 0
    assert profile["quiet_window_sec"] > 0
    assert profile["prompt_wait_sec"] > 0
    assert profile["reboot_loop_threshold"] >= 2
    assert profile["recent_lines_limit"] >= 100
    assert profile["max_reassess_rounds"] >= 1
    assert "dmesg" in profile["l1_commands"]
    assert "send_enter" in profile["l2_actions"]


def test_all_five_fixtures_exist_and_nonempty():
    expected = {"normal_boot", "kernel_panic", "boot_hang", "reboot_loop", "no_output"}
    actual = {f.stem for f in FIXTURES_DIR.glob("*.jsonl")}
    assert expected <= actual, f"缺失 fixture: {expected - actual}"

    for stem in expected:
        path = FIXTURES_DIR / f"{stem}.jsonl"
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        assert lines, f"{stem}.jsonl 不能为空"


def test_reboot_loop_fixture_contains_multiple_boot_cycles():
    path = FIXTURES_DIR / "reboot_loop.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(rows) >= 4
    # 至少出现 2 次 reboot 边界
    reboot_count = sum(1 for r in rows if "Restarting system" in r["text"])
    assert reboot_count >= 2


def test_fixture_rows_have_required_fields():
    for f in FIXTURES_DIR.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            assert "t" in row, f"{f.name} 缺 t 字段"
            assert "text" in row, f"{f.name} 缺 text 字段"
            assert isinstance(row["t"], (int, float)), f"{f.name} t 必须是数值"

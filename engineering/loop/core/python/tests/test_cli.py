"""CLI 测试：参数透传、异常兜底、非 PASS 退出码。"""
import json

from loop_core.cli import main


def test_cli_fixture_mode_passes(tmp_path):
    """fixture 模式正常执行返回 0。"""
    suite_path = tmp_path / "t.yaml"
    suite_path.write_text("""
suite: t
version: 1
cases:
  - id: shell_ok
    command: ""
    assert: {type: prompt_visible}
""")
    fixture_path = tmp_path / "f.jsonl"
    fixture_path.write_text('{"t": 1.0, "text": "console:/ $"}\n')
    profile_path = tmp_path / "p.json"
    profile_path.write_text(json.dumps({"device_id": "rp5", "prompt_markers": ["console:/ $"]}))

    artifacts = tmp_path / "out"
    rc = main([
        "run",
        "--suite", str(suite_path),
        "--fixture", str(fixture_path),
        "--device-profile", str(profile_path),
        "--case-dirs", str(tmp_path),
        "--artifacts-dir", str(artifacts),
    ])
    assert rc == 0
    assert (artifacts / "evidence_bundle.json").exists()


def test_cli_returns_nonzero_on_fail(tmp_path):
    """suite fail 时返回非零。"""
    suite_path = tmp_path / "t.yaml"
    suite_path.write_text("""
suite: t
version: 1
cases:
  - id: must_fail
    command: "true"
    assert: {type: contains, value: "impossible_match"}
    severity: critical
""")
    fixture_path = tmp_path / "f.jsonl"
    fixture_path.write_text('{"t": 0.5, "text": "some output"}\n{"t": 0.6, "text": "console:/ $"}\n')
    profile_path = tmp_path / "p.json"
    profile_path.write_text(json.dumps({"device_id": "rp5"}))

    artifacts = tmp_path / "out"
    rc = main([
        "run",
        "--suite", str(suite_path),
        "--fixture", str(fixture_path),
        "--device-profile", str(profile_path),
        "--case-dirs", str(tmp_path),
        "--artifacts-dir", str(artifacts),
    ])
    assert rc == 1


def test_cli_bundle_contains_execution_config(tmp_path):
    """生成的 bundle 包含 execution_config。"""
    suite_path = tmp_path / "t.yaml"
    suite_path.write_text("""
suite: t
version: 1
cases:
  - id: c1
    command: ""
    assert: {type: prompt_visible}
""")
    fixture_path = tmp_path / "f.jsonl"
    fixture_path.write_text('{"t": 1.0, "text": "console:/ $"}\n')
    profile_path = tmp_path / "p.json"
    profile_path.write_text(json.dumps({"device_id": "rp5", "prompt_markers": ["console:/ $"]}))

    artifacts = tmp_path / "out"
    main([
        "run", "--suite", str(suite_path),
        "--fixture", str(fixture_path),
        "--device-profile", str(profile_path),
        "--case-dirs", str(tmp_path),
        "--artifacts-dir", str(artifacts),
        "--capture-timeout", "3.0",
    ])
    bundle = json.loads((artifacts / "evidence_bundle.json").read_text())
    assert bundle["execution_config"]["capture_timeout"] == 3.0
    assert "provider_type" in bundle["execution_config"]


def test_cli_produces_bundle_on_runtime_exception(tmp_path, monkeypatch):
    """runner.run() 抛异常时，CLI 顶层兜底仍写出 failure bundle。"""
    import loop_core.cli as cli_mod

    suite_path = tmp_path / "t.yaml"
    suite_path.write_text("""
suite: t
version: 1
cases:
  - id: c1
    command: ""
    assert: {type: prompt_visible}
""")
    fixture_path = tmp_path / "f.jsonl"
    fixture_path.write_text('{"t": 1.0, "text": "console:/ $"}\n')
    profile_path = tmp_path / "p.json"
    profile_path.write_text(json.dumps({"device_id": "rp5", "prompt_markers": ["console:/ $"]}))

    artifacts = tmp_path / "out"

    original_run = cli_mod.LoopRunner.run

    def boom(self):
        raise RuntimeError("boom-from-transport")

    monkeypatch.setattr(cli_mod.LoopRunner, "run", boom)
    try:
        rc = main([
            "run", "--suite", str(suite_path),
            "--fixture", str(fixture_path),
            "--device-profile", str(profile_path),
            "--case-dirs", str(tmp_path),
            "--artifacts-dir", str(artifacts),
        ])
    finally:
        monkeypatch.setattr(cli_mod.LoopRunner, "run", original_run)

    # 兜底产出的 bundle overall=FAIL → 退出码非零
    assert rc == 1
    bundle = json.loads((artifacts / "evidence_bundle.json").read_text())
    assert bundle["summary"]["overall"] == "FAIL"
    assert "boom-from-transport" in bundle["summary"]["error"]


def test_cli_passes_boot_panic_markers_to_runner(tmp_path, monkeypatch):
    """CLI 从 DeviceProfile 提取 boot_markers/panic_markers 传给 LoopRunner。"""
    from loop_core import cli

    captured = {}

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-test", device_id="rp5", suite="test",
                timestamp="2026-01-01T00:00:00+0800",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "PASS"},
                cases=[], evidence={},
            )

        def build_failure_bundle(self, reason):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-fail", device_id="rp5", suite="test",
                timestamp="2026-01-01T00:00:00+0800",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "FAIL", "error": reason},
                cases=[], evidence={},
            )

    profile_file = tmp_path / "profile.json"
    profile_file.write_text('{"device_id":"rp5","boot_markers":["Booting Linux"],"panic_markers":["Kernel panic"]}', encoding="utf-8")

    monkeypatch.setattr(cli, "LoopRunner", FakeRunner)

    (tmp_path / "dummy.yaml").write_text("suite: test\nversion: 1\ncases: []\n", encoding="utf-8")
    (tmp_path / "dummy.jsonl").write_text('{"t":0,"text":"x"}\n', encoding="utf-8")

    argv = [
        "run",
        "--suite", str(tmp_path / "dummy.yaml"),
        "--fixture", str(tmp_path / "dummy.jsonl"),
        "--device-profile", str(profile_file),
        "--artifacts-dir", str(tmp_path / "out"),
    ]
    cli.main(argv)

    assert captured.get("boot_markers") == ["Booting Linux"]
    assert captured.get("panic_markers") == ["Kernel panic"]


def test_cli_live_mode_uses_provider_loader(tmp_path, monkeypatch):
    import loop_core.cli as cli

    captured = {}

    class FakeTransport:
        def acquire_writer(self):
            return True

        def release(self):
            pass

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-test",
                device_id="rp5",
                suite="test",
                timestamp="2026-06-21T00:00:00+08:00",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "PASS"},
                cases=[],
                evidence={},
            )

        def build_failure_bundle(self, reason):
            raise AssertionError(reason)

    def fake_build_live_transport(profile, args):
        captured["transport_name"] = profile.transport
        captured["adb_endpoint"] = args.adb_endpoint
        return FakeTransport()

    monkeypatch.setattr(cli, "build_live_transport", fake_build_live_transport)
    monkeypatch.setattr(cli, "LoopRunner", FakeRunner)

    suite_path = tmp_path / "t.yaml"
    suite_path.write_text("suite: test\nversion: 1\ncases: []\n", encoding="utf-8")
    profile_path = tmp_path / "p.json"
    profile_path.write_text('{"device_id":"rp5","transport":"adb"}', encoding="utf-8")

    rc = cli.main([
        "run",
        "--suite", str(suite_path),
        "--device-profile", str(profile_path),
        "--artifacts-dir", str(tmp_path / "out"),
        "--adb-endpoint", "192.168.1.55:5555",
    ])

    assert rc == 0
    assert captured["transport_name"] == "adb"
    assert captured["adb_endpoint"] == "192.168.1.55:5555"


def test_cli_adb_suite_path_keeps_case_dirs(tmp_path, monkeypatch):
    import loop_core.cli as cli

    captured = {}

    class FakeTransport:
        def acquire_writer(self):
            return True

        def release(self):
            pass

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-test",
                device_id="rp5",
                suite="system.adb_shell",
                timestamp="2026-06-21T00:00:00+08:00",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "PASS"},
                cases=[],
                evidence={},
            )

        def build_failure_bundle(self, reason):
            raise AssertionError(reason)

    monkeypatch.setattr(cli, "build_live_transport", lambda profile, args: FakeTransport())
    monkeypatch.setattr(cli, "LoopRunner", FakeRunner)

    suite_path = tmp_path / "adb-shell-success.yaml"
    suite_path.write_text("suite: system.adb_shell\nversion: 1\ncases: []\n", encoding="utf-8")
    profile_path = tmp_path / "adb.json"
    profile_path.write_text('{"device_id":"rp5","transport":"adb"}', encoding="utf-8")

    rc = cli.main([
        "run",
        "--suite", str(suite_path),
        "--device-profile", str(profile_path),
        "--case-dirs", str(tmp_path),
        "--artifacts-dir", str(tmp_path / "out"),
        "--adb-endpoint", "192.168.1.55:5555",
    ])

    assert rc == 0
    assert captured["suite"].name == "system.adb_shell"

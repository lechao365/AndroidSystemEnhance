"""deployer 单元测试（skip/flash_full/dd 检测逻辑）。"""
from loop_deploy.models import DeployPlan, DeployMode, DeployTarget
from loop_deploy.deployer import Deployer
from loop_adb.client import AdbCommandResult


def _make_fake_runner(results: dict):
    """构造 FakeRunner：按 argv 首元素关键字匹配返回预设结果。

    results: {keyword: AdbCommandResult | AdbShellResult | list}
    支持同一 keyword 多次调用（list 逐个弹出）。
    """
    pending = {k: (list(v) if isinstance(v, list) else [v]) for k, v in results.items()}

    def runner(argv, timeout_sec):
        cmd_str = " ".join(argv)
        for keyword, queue in pending.items():
            if keyword in cmd_str and queue:
                item = queue.pop(0)
                if callable(item):
                    return item(argv, timeout_sec)
                return item
        raise AssertionError(f"FakeRunner: no mock for {' '.join(argv[:4])}")

    return runner


def test_skip_returns_success():
    import loop_adb.client as mod
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555")
    d = Deployer(client)
    plan = DeployPlan.skip("no changes")
    result = d.deploy(plan, [])
    assert result.success
    assert result.mode == DeployMode.SKIP


def test_flash_full_returns_error():
    import loop_adb.client as mod
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555")
    d = Deployer(client)
    plan = DeployPlan.flash_full(["foo.te"])
    result = d.deploy(plan, [])
    assert not result.success
    assert "FLASH_FULL" in result.error


def test_dd_boot_aborts_on_dd_failure(tmp_path):
    """dd 写分区失败时，Deployer 应立即返回失败而非继续 reboot。"""
    import hashlib
    import loop_adb.client as mod

    boot_img = tmp_path / "boot.img"
    boot_img.write_bytes(b"\0" * 8192)
    expected_sha = hashlib.sha256(b"\0" * 8192).hexdigest()

    def ok_cmd(argv, timeout_sec):
        return AdbCommandResult(argv=list(argv), exit_code=0, stdout="", stderr="")

    def sha_ok(argv, timeout_sec):
        return AdbCommandResult(
            argv=list(argv), exit_code=0,
            stdout=f"{expected_sha}  /data/local/tmp/boot.img\n__LE_EXIT_CODE__=0\n",
            stderr="",
        )

    def boot_ok(argv, timeout_sec):
        return AdbCommandResult(
            argv=list(argv), exit_code=0,
            stdout="1\n__LE_EXIT_CODE__=0\n", stderr="",
        )

    def dd_fail(argv, timeout_sec):
        return AdbCommandResult(
            argv=list(argv), exit_code=0,
            stdout="dd: write error: No space left on device\n__LE_EXIT_CODE__=1\n",
            stderr="",
        )

    runner = _make_fake_runner({
        "root": ok_cmd,
        "push": ok_cmd,
        "sha256sum": sha_ok,
        "getprop sys.boot_completed": boot_ok,
        "dd if=/dev/block/mmcblk0p1 bs=4M": ok_cmd,  # 备份读取（L147）
        "dd if=/dev/block/mmcblk0p1 of=": ok_cmd,    # 设备端备份写入（L161）
        "dd if=/data/local/tmp/boot.img": dd_fail,   # 正式 dd 写入（L168）
    })

    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    d = Deployer(client)
    plan = DeployPlan(
        mode=DeployMode.DD_BOOT_REBOOT, changed_files=["kernel/foo.c"],
        reason="test", build_targets=[], deploy_targets=[],
        requires_reboot=True, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(boot_img)])
    assert not result.success
    assert "dd write failed" in result.error


def test_find_artifact_uses_os_walk(tmp_path):
    """_find_artifact 在 aosp_out 目录下通过 os.walk 递归查找。"""
    import loop_adb.client as mod

    nested = tmp_path / "out" / "target" / "product" / "rpi5"
    nested.mkdir(parents=True)
    target_file = nested / "lechao_lciod_hal"
    target_file.write_bytes(b"\0" * 64)

    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555")
    d = Deployer(client, aosp_out=str(tmp_path / "out"))
    found = d._find_artifact([], "lechao_lciod_hal")
    assert found.endswith("lechao_lciod_hal")
    assert "rpi5" in found

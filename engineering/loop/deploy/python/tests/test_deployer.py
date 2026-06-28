"""deployer 单元测试（skip/flash_full/dd 检测逻辑）。"""
from loop_deploy.models import DeployPlan, DeployMode, DeployTarget
from loop_deploy.deployer import Deployer
from loop_adb.client import AdbCommandResult


def _make_fake_runner(results: dict):
    """构造 FakeRunner：按 argv 首元素关键字匹配返回预设结果。

    results: {keyword: AdbCommandResult | AdbShellResult | list | callable}
    - list 类型：逐个弹出（模拟多次调用返回不同结果）
    - callable / 单值类型：每次调用都返回该值（永久可重复调用）
    """
    # 分离永久 queue（callable/单值）和一次性 queue（list）
    permanent: dict[str, object] = {}
    oneshot: dict[str, list] = {}
    for k, v in results.items():
        if isinstance(v, list):
            oneshot[k] = list(v)
        else:
            permanent[k] = v

    def runner(argv, timeout_sec):
        cmd_str = " ".join(argv)
        # 先检查一次性 queue（优先级高，模拟序列化行为）
        for keyword, queue in oneshot.items():
            if keyword in cmd_str and queue:
                item = queue.pop(0)
                if callable(item):
                    return item(argv, timeout_sec)
                return item
        # 再检查永久 queue
        for keyword, item in permanent.items():
            if keyword in cmd_str:
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

    def ok_shell(argv, timeout_sec):
        return AdbCommandResult(argv=list(argv), exit_code=0, stdout="__LE_EXIT_CODE__=0\n", stderr="")

    def df_ok(argv, timeout_sec):
        return AdbCommandResult(
            argv=list(argv), exit_code=0,
            stdout="/data 16384 8192 8388608 /data\n__LE_EXIT_CODE__=0\n", stderr="",
        )

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
        "df /data": df_ok,
        "dd if=/dev/block/mmcblk0p1 bs=4M": ok_shell,  # 备份读取
        "dd if=/dev/block/mmcblk0p1 of=": ok_shell,    # 设备端备份写入
        "dd if=/data/local/tmp/boot.img": dd_fail,     # 正式 dd 写入
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


# ---------------------------------------------------------------------------
# Phase B 新增：dd 防护网测试
# ---------------------------------------------------------------------------

def _make_dd_ok_runner(tmp_path, boot_content=None, df_output=None, root_result=None,
                       backup_read_result=None):
    """构造一个能走通 dd 全流程（到 panic 检测前）的 fake runner。"""
    import hashlib
    content = boot_content or (b"\0" * 8192)
    boot_img = tmp_path / "boot.img"
    boot_img.write_bytes(content)
    expected_sha = hashlib.sha256(content).hexdigest()

    # shell 命令返回必须含 __LE_EXIT_CODE__ marker（AdbClient.shell 解析）
    def ok_shell(argv, timeout_sec):
        return AdbCommandResult(argv=list(argv), exit_code=0, stdout="__LE_EXIT_CODE__=0\n", stderr="")

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

    def df_ok(argv, timeout_sec):
        return AdbCommandResult(
            argv=list(argv), exit_code=0,
            stdout=df_output or "/data 16384 8192 8388608 /data\n__LE_EXIT_CODE__=0\n",
            stderr="",
        )

    runner = _make_fake_runner({
        "root": root_result or ok_shell,
        "push": ok_shell,
        "sha256sum": sha_ok,
        "getprop sys.boot_completed": boot_ok,
        "dd if=/dev/block/mmcblk0p1 bs=4M": backup_read_result or ok_shell,
        "dd if=/dev/block/mmcblk0p1 of=": ok_shell,
        "dd if=/data/local/tmp/boot.img": ok_shell,
        "df /data": df_ok,
        "sync": ok_shell,
        "rm /data/local/tmp/boot.img": ok_shell,
        "reboot": ok_shell,
    })
    return boot_img, runner


def test_dd_aborts_when_root_fails(tmp_path):
    """dd 路径 adb root 失败时必须立即中止（不 push/不 dd）。

    回归 P0-5：dd 路径 `self._client.root()` 返回值被丢弃，root 失败仍继续
    push + dd（adbd 非 root 时 dd 静默失败）。修复后须返回 ADB_ROOT_FAILED。
    """
    import loop_adb.client as mod

    def root_fail(argv, timeout_sec):
        return AdbCommandResult(argv=list(argv), exit_code=1, stdout="", stderr="adbd cannot run as root")

    boot_img, runner = _make_dd_ok_runner(tmp_path, root_result=root_fail)
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    d = Deployer(client)
    plan = DeployPlan(
        mode=DeployMode.DD_BOOT_REBOOT, changed_files=["kernel/foo.c"],
        reason="test", build_targets=[], deploy_targets=[],
        requires_reboot=True, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(boot_img)])
    assert not result.success
    assert result.error_code.value == "ADB_ROOT_FAILED"


def test_dd_aborts_on_disk_check_unparseable(tmp_path):
    """df 输出无法解析出可用空间时必须 fail-closed（DISK_CHECK_FAILED），不 dd。

    回归 P0-4：df 解析失败（parts<4 / int 失败 / df 命令失败）原仅 warning 后继续 dd
    （空间不确定下执行不可逆 dd 写分区）。修复后须中止。
    """
    import loop_adb.client as mod
    boot_img, runner = _make_dd_ok_runner(
        tmp_path, df_output="garbage\n__LE_EXIT_CODE__=0\n",
    )
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    d = Deployer(client)
    plan = DeployPlan(
        mode=DeployMode.DD_BOOT_REBOOT, changed_files=["kernel/foo.c"],
        reason="test", build_targets=[], deploy_targets=[],
        requires_reboot=True, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(boot_img)])
    assert not result.success
    assert result.error_code.value == "DISK_CHECK_FAILED"


def test_dd_aborts_when_host_backup_fails(tmp_path):
    """host 备份创建失败时必须 fail-closed（BACKUP_FAILED），不 dd。

    回归 P0-4：备份读取异常原被吞掉（backup_sha=""/host_backup=""）并跳过完整性
    校验后继续 dd（无可用回滚备份仍写设备）。修复后须中止。
    """
    import loop_adb.client as mod

    def backup_read_oserror(argv, timeout_sec):
        raise OSError("device backup read failed")

    boot_img, runner = _make_dd_ok_runner(tmp_path, backup_read_result=backup_read_oserror)
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    d = Deployer(client)
    plan = DeployPlan(
        mode=DeployMode.DD_BOOT_REBOOT, changed_files=["kernel/foo.c"],
        reason="test", build_targets=[], deploy_targets=[],
        requires_reboot=True, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(boot_img)])
    assert not result.success
    assert result.error_code.value == "BACKUP_FAILED"


def test_dd_panic_detected_via_serial(tmp_path):
    """serial shell 返回 kernel panic → Deployer 判定 KERNEL_PANIC。"""
    import loop_adb.client as mod
    boot_img, runner = _make_dd_ok_runner(tmp_path)

    # mock AdbOps.wait_boot_completed 返回 True
    def serial_panic(cmd):
        return "Kernel panic - not syncing: Attempted to kill init"

    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    d = Deployer(client, serial_shell_provider=serial_panic)
    plan = DeployPlan(
        mode=DeployMode.DD_BOOT_REBOOT, changed_files=["kernel/foo.c"],
        reason="test", build_targets=[], deploy_targets=[],
        requires_reboot=True, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(boot_img)])
    assert not result.success
    assert result.error_code.value == "KERNEL_PANIC"
    assert "serial" in result.error


def test_dd_no_serial_no_adb_fails_safe(tmp_path):
    """serial=None + adb reboot 后不可达 → fail-safe 判定 KERNEL_PANIC。"""
    import loop_adb.client as mod
    boot_img, runner = _make_dd_ok_runner(tmp_path)

    # reboot 后 adb 不可达：用 wrapper 在 reboot 后将 shell 替换为抛异常
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    d = Deployer(client, serial_shell_provider=None)

    # monkey-patch reboot：reboot 后将 shell/logcat/connect 都替换为不可达
    original_reboot = client.reboot

    def reboot_and_disconnect(timeout_sec=15.0):
        original_reboot(timeout_sec)
        # reboot 后 adb 断连
        def unreachable(*a, **kw):
            raise Exception("adb unreachable: device not responding")
        client.shell = unreachable
        client.logcat = unreachable
        client.connect = lambda timeout_sec: None

    client.reboot = reboot_and_disconnect
    # wait_boot_completed 返回 True（模拟 boot_completed 标记到达但 adb 随即断连）
    d._ops.wait_boot_completed = lambda timeout: True

    plan = DeployPlan(
        mode=DeployMode.DD_BOOT_REBOOT, changed_files=["kernel/foo.c"],
        reason="test", build_targets=[], deploy_targets=[],
        requires_reboot=True, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(boot_img)])
    assert not result.success
    assert result.error_code.value == "KERNEL_PANIC"
    assert "fail-safe" in result.error


def test_dd_aborts_on_disk_full(tmp_path):
    """df /data 返回空间不足 → DISK_FULL。"""
    import loop_adb.client as mod
    # avail=1KB (boot_img 是 8192 字节，需要至少 16384 字节 = 16KB)
    boot_img, runner = _make_dd_ok_runner(
        tmp_path,
        df_output="/data 16384 16384 1 /data\n__LE_EXIT_CODE__=0\n",  # avail=1KB
    )

    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    d = Deployer(client)
    plan = DeployPlan(
        mode=DeployMode.DD_BOOT_REBOOT, changed_files=["kernel/foo.c"],
        reason="test", build_targets=[], deploy_targets=[],
        requires_reboot=True, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(boot_img)])
    assert not result.success
    assert result.error_code.value == "DISK_FULL"


def test_push_single_warns_on_backup_fail(tmp_path):
    """adb pull 备份失败 → warning 记录但不中断部署。"""
    import loop_adb.client as mod

    def ok_shell(argv, timeout_sec):
        return AdbCommandResult(argv=list(argv), exit_code=0, stdout="__LE_EXIT_CODE__=0\n", stderr="")

    def pull_fail(src, dst, timeout_sec):
        raise Exception("device offline")

    runner = _make_fake_runner({
        "root": ok_shell,
        "remount": ok_shell,
        "push": ok_shell,
        "setprop": ok_shell,
        "getprop": ok_shell,
        "logcat": ok_shell,
    })

    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=runner)
    client.pull = pull_fail  # 模拟备份失败
    # mock wait_service_running
    from loop_deploy.deployer import Deployer
    d = Deployer(client)
    d._ops.wait_service_running = lambda name, timeout: True

    artifact = tmp_path / "test_hal"
    artifact.write_bytes(b"\0" * 64)

    plan = DeployPlan(
        mode=DeployMode.PUSH_SINGLE, changed_files=["foo.cpp"],
        reason="test", build_targets=["foo"],
        deploy_targets=[DeployTarget(artifact_name="test_hal", remote_path="/vendor/bin/hw/test_hal", service_name="test_hal")],
        requires_reboot=False, estimated_seconds=10,
    )
    result = d.deploy(plan, [str(artifact)])
    assert result.success
    assert len(result.warnings) > 0
    assert "backup failed" in result.warnings[0]

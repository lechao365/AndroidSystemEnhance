# ws_upload_tests.py 单测：设备侧单测执行环节的解析/定位/编排逻辑。
# 关键场景：yaml test_targets 读取、nativetest 二进制定位、push+执行
# 汇总 pass/fail、二进制缺失判红、设备不可达退 1。

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_upload_tests as wu  # noqa: E402


class TestLoadTestTargets(unittest.TestCase):
    def test_reads_all_modules_test_targets(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text(
                "modules:\n"
                "  lcview:\n"
                "    test_targets: [lechao_lcview_unit_test, lechao_lcview_hal_test]\n"
                "    test_targets_run_as_root: []\n"
                "    test_src: vendor/lechao/services/lechao_lcview\n"
                "  lciod:\n"
                "    test_targets: [lechao_lciod_unit_test, lechao_lciod_hal_test]\n"
                "    test_targets_run_as_root: [lechao_lciod_hal_test]\n"
                "    test_src: vendor/lechao/services/lechao_lciod\n",
                encoding="utf-8")
            targets, run_as_root, src_map, err = wu.load_test_targets(str(cfg))
        self.assertIsNone(err)
        self.assertEqual(targets,
                         ["lechao_lcview_unit_test", "lechao_lcview_hal_test",
                          "lechao_lciod_unit_test", "lechao_lciod_hal_test"])
        self.assertEqual(run_as_root, {"lechao_lciod_hal_test"})
        # src_map 按目标名映射模块 test_src（推送前新鲜度校验用）
        self.assertEqual(src_map["lechao_lcview_unit_test"],
                         "vendor/lechao/services/lechao_lcview")
        self.assertEqual(src_map["lechao_lciod_hal_test"],
                         "vendor/lechao/services/lechao_lciod")

    def test_missing_targets_returns_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text("modules: {}\n", encoding="utf-8")
            targets, run_as_root, src_map, err = wu.load_test_targets(str(cfg))
        self.assertIsNone(targets)
        self.assertIsNone(run_as_root)
        self.assertIsNone(src_map)
        self.assertIn("无 test_targets", err)

    def test_no_test_src_skips_src_map(self):
        # 模块未登记 test_src 时不产出 src_map 项（不误伤，跳过新鲜度校验）
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text(
                "modules:\n"
                "  mod:\n"
                "    test_targets: [t1]\n"
                "    test_targets_run_as_root: []\n",
                encoding="utf-8")
            targets, run_as_root, src_map, err = wu.load_test_targets(str(cfg))
        self.assertIsNone(err)
        self.assertNotIn("t1", src_map)


class TestFindBinary(unittest.TestCase):
    def test_finds_nativetest64(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
            p.parent.mkdir(parents=True)
            p.write_text("x")
            got = wu.find_binary(str(out), "rpi5", "t1")
        self.assertEqual(got, str(p))

    def test_finds_testcases_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            p = out / "target" / "product" / "rpi5" / "testcases" / "t1" / "arm64" / "t1"
            p.parent.mkdir(parents=True)
            p.write_text("x")
            got = wu.find_binary(str(out), "rpi5", "t1")
        self.assertEqual(got, str(p))

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            got = wu.find_binary(str(Path(d) / "out"), "rpi5", "t1")
        self.assertIsNone(got)


class TestBinaryIsStale(unittest.TestCase):
    def _mk(self):
        """构造 aosp_root/src 目录 + 二进制，返回 (aosp_root, src_rel, binary)。"""
        d = Path(tempfile.mkdtemp())
        aosp_root = d / "aosp"
        src = aosp_root / "src"
        src.mkdir(parents=True)
        binary = d / "bin"
        binary.write_text("x")
        return aosp_root, "src", binary

    def test_fresh_binary_not_stale(self):
        # 二进制比源码新（编译产物更新）→ 不陈旧
        aosp_root, src_rel, binary = self._mk()
        src_file = aosp_root / src_rel / "a.cpp"
        src_file.write_text("old")
        os.utime(src_file, (0, 0))  # 源码 mtime 早于二进制
        self.assertFalse(wu.binary_is_stale(str(binary), str(aosp_root), src_rel))

    def test_source_newer_is_stale(self):
        # 源码任一文件比二进制新（新增用例后未重编）→ 陈旧判红
        aosp_root, src_rel, binary = self._mk()
        src_file = aosp_root / src_rel / "b.cpp"
        src_file.write_text("new")
        os.utime(src_file, (time.time() + 10, time.time() + 10))  # mtime 晚于二进制
        self.assertTrue(wu.binary_is_stale(str(binary), str(aosp_root), src_rel))

    def test_missing_src_dir_not_stale(self):
        # 源码目录缺失 → 不判陈旧（产物缺失由调用方判红，避免误伤）
        aosp_root, src_rel, binary = self._mk()
        self.assertFalse(
            wu.binary_is_stale(str(binary), str(aosp_root), "no_such_dir"))


class TestRunOne(unittest.TestCase):
    def test_pass_returns_ok(self):
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # readback sha 失败（幂等跳过不生效）
            ("", 1),  # readback bytes 失败
            ("", 0),  # push
            ("", 0),  # chmod
            ("[==========] 42 tests from 2 test suites ran. (100 ms total)\n", 0),
        ]):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "out"
                p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
                p.parent.mkdir(parents=True)
                p.write_text("x")
                ok, detail = wu.run_one("ep", str(out), "rpi5", "t1")
        self.assertTrue(ok)
        self.assertIn("PASS", detail)
        self.assertIn("42 tests", detail)

    def test_pass_stats_failed_zero(self):
        # 方向 8：成功路径（rc=0 且无 FAILED 行）failed 显式落 0——gtest
        # 全过输出无「[  FAILED  ] N tests」汇总行，None 会被 ws_report
        # 误判非全绿（-sv 真机单测产物首次暴露）
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # readback sha 失败（幂等跳过不生效）
            ("", 1),  # readback bytes 失败
            ("", 0),  # push
            ("", 0),  # chmod
            ("[==========] 42 tests from 2 test suites ran. (100 ms total)\n"
             "[  PASSED  ] 42 tests.\n", 0),
        ]):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "out"
                p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
                p.parent.mkdir(parents=True)
                p.write_text("x")
                ok, detail, stats = wu.run_one(
                    "ep", str(out), "rpi5", "t1", return_stats=True)
        self.assertTrue(ok)
        self.assertEqual(stats["rc"], 0)
        self.assertEqual(stats["tests"], 42)
        self.assertEqual(stats["failed"], 0)

    def test_fail_returns_not_ok_with_output(self):
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # readback sha 失败（幂等跳过不生效）
            ("", 1),  # readback bytes 失败
            ("", 0),  # push
            ("", 0),  # chmod
            ("[  FAILED  ] t1.T_Broken\n", 1),
        ]):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "out"
                p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
                p.parent.mkdir(parents=True)
                p.write_text("x")
                ok, detail = wu.run_one("ep", str(out), "rpi5", "t1")
        self.assertFalse(ok)
        self.assertIn("FAIL", detail)

    def test_push_failure_returns_not_ok(self):
        with mock.patch.object(wu, "adb_run", return_value=("", 1)):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "out"
                p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
                p.parent.mkdir(parents=True)
                p.write_text("x")
                ok, detail = wu.run_one("ep", str(out), "rpi5", "t1")
        self.assertFalse(ok)
        self.assertIn("push 失败", detail)

    def test_stale_binary_rejected_before_push(self):
        # 产物陈旧（源码比二进制新）→ 推送前判红，不得报绿（防推旧二进制）
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            aosp_root = root / "aosp"
            src = aosp_root / "src"
            src.mkdir(parents=True)
            src_file = src / "t.cpp"
            src_file.write_text("new")
            out = root / "out"
            p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
            p.parent.mkdir(parents=True)
            p.write_text("x")
            os.utime(src_file, (time.time() + 10, time.time() + 10))  # 源码比二进制新
            ok, detail = wu.run_one("ep", str(out), "rpi5", "t1",
                                    aosp_root=str(aosp_root), src_rel="src")
        self.assertFalse(ok)
        self.assertIn("陈旧", detail)
        self.assertIn("重新编译", detail)

    def test_missing_binary_returns_not_ok(self):
        with tempfile.TemporaryDirectory() as d:
            ok, detail = wu.run_one("ep", str(Path(d) / "out"), "rpi5", "t1")
        self.assertFalse(ok)
        self.assertIn("编译产物缺失", detail)

    def test_zero_cases_rejected(self):
        # 方向 5：汇总行解析到但用例数为 0 → 判红（无用例被执行禁止报绿）
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # readback sha 失败（幂等跳过不生效）
            ("", 1),  # readback bytes 失败
            ("", 0),  # push
            ("", 0),  # chmod
            ("[==========] 0 tests from 0 test suites ran. (1 ms total)\n", 0),
        ]):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "out"
                p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
                p.parent.mkdir(parents=True)
                p.write_text("x")
                ok, detail = wu.run_one("ep", str(out), "rpi5", "t1")
        self.assertFalse(ok)
        self.assertIn("用例数为 0", detail)

    def test_missing_summary_rejected(self):
        # 方向 5：汇总行解析不到 → 判红（无法证明用例真实执行）
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # readback sha 失败（幂等跳过不生效）
            ("", 1),  # readback bytes 失败
            ("", 0),  # push
            ("", 0),  # chmod
            ("[ RUN      ] t1.T1\n[       OK ] t1.T1 (1 ms)\n", 0),
        ]):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "out"
                p = out / "target" / "product" / "rpi5" / "data" / "nativetest64" / "t1" / "t1"
                p.parent.mkdir(parents=True)
                p.write_text("x")
                ok, detail = wu.run_one("ep", str(out), "rpi5", "t1")
        self.assertFalse(ok)
        self.assertIn("用例数解析不到", detail)


class TestDeviceBinaryFingerprint(unittest.TestCase):
    """幂等推送比对基准：设备侧 sha256+bytes 回读，任一缺失返回 None
    （回读不可信不得跳过推送，对齐 ws_push.readback_device 口径）。"""

    def test_reads_sha_and_bytes(self):
        # sha 用合法 64 位十六进制（实现对 sha256sum 输出做格式校验，
        # 回读非 {0-9a-f}{64} 视为不可信 → None）
        sha = hashlib.sha256(b"x").hexdigest()
        with mock.patch.object(wu, "adb_run", side_effect=[
            (f"{sha}  /data/local/tmp/t1", 0),
            ("4096", 0),
        ]) as m:
            fp = wu.device_binary_fingerprint("ep", "t1")
        self.assertEqual(fp, {"sha256": sha, "bytes": 4096})
        self.assertEqual(m.call_args_list[0].args[1],
                         ["shell", "sha256sum /data/local/tmp/t1"])
        self.assertEqual(m.call_args_list[1].args[1],
                         ["shell", "stat -c %s /data/local/tmp/t1"])

    def test_missing_sha_returns_none(self):
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # sha256sum 失败
            ("4096", 0),
        ]):
            self.assertIsNone(wu.device_binary_fingerprint("ep", "t1"))

    def test_missing_bytes_returns_none(self):
        sha = hashlib.sha256(b"x").hexdigest()
        with mock.patch.object(wu, "adb_run", side_effect=[
            (f"{sha}  /data/local/tmp/t1", 0),
            ("", 1),  # stat 失败
        ]):
            self.assertIsNone(wu.device_binary_fingerprint("ep", "t1"))


class TestIdempotentPush(unittest.TestCase):
    """方向 A5：推送前回读设备侧指纹，与本地全等跳过 adb push（二进制
    未变不重推）；sha 等字节不等不跳；回读缺失照推。"""

    GTEST_OK = ("[==========] 42 tests from 2 test suites ran. "
                "(100 ms total)\n")

    def _mk_binary(self, content=b"x"):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = (Path(d) / "out" / "target" / "product" / "rpi5"
             / "data" / "nativetest64" / "t1" / "t1")
        p.parent.mkdir(parents=True)
        p.write_bytes(content)
        return str(Path(d) / "out"), d

    def test_identical_skips_push(self):
        content = b"same-bytes"
        out, _ = self._mk_binary(content)
        sha = hashlib.sha256(content).hexdigest()
        with mock.patch.object(wu, "adb_run", side_effect=[
            (f"{sha}  /data/local/tmp/t1", 0),  # readback sha
            (str(len(content)), 0),             # readback bytes
            ("", 0),                            # chmod
            (self.GTEST_OK, 0),                 # gtest
        ]) as m:
            ok, detail, stats = wu.run_one("ep", out, "rpi5", "t1",
                                           return_stats=True)
        cmds = [c.args[1][0] for c in m.call_args_list]
        self.assertNotIn("push", cmds)
        self.assertTrue(ok)
        self.assertIn("PASS", detail)

    def test_same_sha_diff_bytes_still_pushes(self):
        content = b"same-bytes"
        out, _ = self._mk_binary(content)
        sha = hashlib.sha256(content).hexdigest()
        with mock.patch.object(wu, "adb_run", side_effect=[
            (f"{sha}  /data/local/tmp/t1", 0),  # readback sha 全等
            ("1", 0),                           # bytes 不等
            ("", 0),                            # push
            ("", 0),                            # chmod
            (self.GTEST_OK, 0),                 # gtest
        ]) as m:
            ok, detail, stats = wu.run_one("ep", out, "rpi5", "t1",
                                           return_stats=True)
        cmds = [c.args[1][0] for c in m.call_args_list]
        self.assertIn("push", cmds)
        self.assertEqual(stats["pushed"], True)

    def test_readback_missing_still_pushes(self):
        out, _ = self._mk_binary()
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),        # readback sha 失败
            ("", 1),        # readback bytes 失败
            ("", 0),        # push
            ("", 0),        # chmod
            (self.GTEST_OK, 0),
        ]) as m:
            ok, detail, stats = wu.run_one("ep", out, "rpi5", "t1",
                                           return_stats=True)
        cmds = [c.args[1][0] for c in m.call_args_list]
        self.assertIn("push", cmds)
        self.assertTrue(ok)
        self.assertEqual(stats["pushed"], True)


class TestMain(unittest.TestCase):
    """wu.main 完整编排（含 _mark_stage 自动打点）：须隔离 CDP_PROJECT_ROOT。

    _mark_stage 无显式 batch 时按 current-batch.json 回落定位批次（cdp_timing
    方向 3），不隔离会打到真实当前批次打点文件（selfcheck 全仓 pytest 时
    污染当批 timings）。打点非本类验证目标，隔离到临时目录即可。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_root = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old_root
        self._tmp.cleanup()

    def test_all_pass_returns_0(self):
        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep") as ec, \
                mock.patch.object(wu, "ensure_user", return_value=(True, "")), \
                mock.patch.object(wu, "run_one", return_value=(
                    True, "t1: PASS", {"name": "t1", "rc": 0,
                                       "tests": 42, "failed": 0})):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 0)
        ec.assert_called_once()

    def test_fail_returns_1(self):
        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user", return_value=(True, "")), \
                mock.patch.object(wu, "run_one", return_value=(
                    False, "t1: FAIL", {"name": "t1", "rc": 1,
                                        "tests": 42, "failed": 3})):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 1)

    def test_ensure_user_failure_marks_fail(self):
        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user", return_value=(False, "adb root 失败")), \
                mock.patch.object(wu, "run_one", return_value=(
                    True, "t1: PASS", {"name": "t1", "rc": 0,
                                       "tests": 42, "failed": 0})):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 1)

    def test_device_unreachable_returns_1(self):
        with mock.patch.object(wu.ac, "ensure_connected", return_value=""):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 1)

    def test_exception_in_run_one_still_restores_shell_user(self):
        # 方向 5：run_one 中途抛异常（adb 异常等）→ finally 仍恢复 shell 用户，
        # 防止 adbd 残留 root 态污染后续 verify 环节
        calls = []

        def fake_ensure_user(ep, need_root):
            calls.append(need_root)
            return (True, "")

        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user",
                                  side_effect=fake_ensure_user), \
                mock.patch.object(wu, "run_one",
                                  side_effect=RuntimeError("adb boom")):
            with self.assertRaises(RuntimeError):
                wu.main(["--test-targets", "t1"])
        # 异常传播后仍执行了 shell 用户恢复（need_root=False）
        self.assertIn(False, calls)
        self.assertEqual(calls[-1], False)

    def test_result_file_written(self):
        # 方向 2/3：--result-file 原子写自描述单测产物（run_id + 每 target
        # 返回码/用例数/失败数），无 .tmp 残留
        out_json = Path(self._tmp.name) / "unit-tests.json"
        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user", return_value=(True, "")), \
                mock.patch.object(wu, "run_one", return_value=(
                    True, "t1: PASS", {"name": "t1", "rc": 0,
                                       "tests": 42, "failed": 0})):
            rc = wu.main(["--test-targets", "t1",
                          "--result-file", str(out_json)])
        self.assertEqual(rc, 0)
        self.assertTrue(out_json.is_file())
        self.assertFalse(out_json.with_name(out_json.name + ".tmp").exists())
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertTrue(data["run_id"])
        self.assertEqual(data["targets"][0]["name"], "t1")
        self.assertEqual(data["targets"][0]["rc"], 0)
        self.assertEqual(data["targets"][0]["tests"], 42)
        self.assertEqual(data["targets"][0]["failed"], 0)

    def test_cdp_run_id_injected_used(self):
        # 方向 8：CDP_RUN_ID 注入时产物 run_id 用注入值（与 push/acceptance
        # 三产物同轮同 run_id，ws_report 一致核验依赖此）
        out_json = Path(self._tmp.name) / "unit-tests.json"
        with mock.patch.dict("os.environ", {"CDP_RUN_ID": "shared-run-001"}), \
                mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user", return_value=(True, "")), \
                mock.patch.object(wu, "run_one", return_value=(
                    True, "t1: PASS", {"name": "t1", "rc": 0,
                                       "tests": 42, "failed": 0})):
            rc = wu.main(["--test-targets", "t1",
                          "--result-file", str(out_json)])
        self.assertEqual(rc, 0)
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "shared-run-001")

    def test_result_file_reports_failed_target(self):
        # 方向 2：ensure_user 失败（无 run_one 统计）也进产物，rc=1
        out_json = Path(self._tmp.name) / "unit-tests.json"
        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user", return_value=(False, "adb root 失败")), \
                mock.patch.object(wu, "run_one", return_value=(
                    True, "t1: PASS", {"name": "t1", "rc": 0,
                                       "tests": 42, "failed": 0})):
            rc = wu.main(["--test-targets", "t1",
                          "--result-file", str(out_json)])
        self.assertEqual(rc, 1)
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(data["targets"][0]["name"], "t1")
        self.assertEqual(data["targets"][0]["rc"], 1)
        self.assertIsNone(data["targets"][0]["tests"])


class TestEnsureUser(unittest.TestCase):
    def test_already_running_no_wait(self):
        # 已处于目标用户（输出 already running）→ 不等待不探活（快速返回）
        with mock.patch.object(wu, "adb_run",
                               return_value=("adbd is already running as root",
                                             0)) as m, \
                mock.patch.object(wu.time, "sleep") as sl:
            ok, detail = wu.ensure_user("ep", True)
        self.assertTrue(ok)
        self.assertEqual(sl.call_count, 0)
        self.assertEqual(m.call_count, 1)

    def test_real_switch_waits_and_probes(self):
        # 真实切换（输出 restarting）→ 等待 + shell 探活确认 adbd 就绪
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("restarting adbd as root", 0),
            ("ok", 0),
        ]) as m, mock.patch.object(wu.time, "sleep"):
            ok, detail = wu.ensure_user("ep", True)
        self.assertTrue(ok)
        self.assertEqual(len(m.call_args_list), 2)
        self.assertEqual(m.call_args_list[1].args[1], ["shell", "echo ok"])

    def test_probe_failure_fails(self):
        # 探活始终失败 → False（adbd 未就绪不得继续 push）
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("restarting adbd as root", 0),
            ("", 1), ("", 1), ("", 1),
        ]), mock.patch.object(wu.time, "sleep"):
            ok, detail = wu.ensure_user("ep", False)
        self.assertFalse(ok)
        self.assertIn("探活失败", detail)

    def test_root_failure_fails(self):
        # adb root 自身失败 → False（不进入等待/探活）
        with mock.patch.object(wu, "adb_run", return_value=("denied", 1)):
            ok, detail = wu.ensure_user("ep", True)
        self.assertFalse(ok)
        self.assertIn("adb root 失败", detail)


class TestDefaultOut(unittest.TestCase):
    def _patch_paths(self, env_path_result):
        fake = mock.Mock()
        fake.env_path.return_value = env_path_result
        return mock.patch.dict(sys.modules, {"paths": fake})

    def test_uses_aosp_ws_from_paths(self):
        # 默认 out 走 paths.conf AOSP_WS（单一事实源）→ AOSP_WS/out
        with self._patch_paths("/data/aosp"):
            out = wu._default_out()
        self.assertEqual(out, "/data/aosp/out")

    def test_empty_aosp_ws_falls_back(self):
        # AOSP_WS 为空 → 回退 ~/workspace/aosp/out
        with self._patch_paths(""):
            out = wu._default_out()
        self.assertTrue(out.endswith("workspace/aosp/out"))

    def test_paths_import_failure_falls_back(self):
        # paths 服务异常（无 AGENTS.md 锚点等）→ 回退，不阻断脚本
        fake = mock.Mock()
        fake.env_path.side_effect = RuntimeError("no AGENTS.md")
        with mock.patch.dict(sys.modules, {"paths": fake}):
            out = wu._default_out()
        self.assertTrue(out.endswith("workspace/aosp/out"))


class TestMarkStageDurS(unittest.TestCase):
    """方向 1：_mark_stage 传 --dur-s 脚本自报实测秒数（段耗时取 dur_s，
    相邻差额余量落 gap_before_<name>，脚本启动前 AI 活动不再污染段口径）。"""

    def _capture(self, dur_s=None):
        captured = {}

        def fake_run(args, **kw):
            captured["args"] = args
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(wu.subprocess, "run", side_effect=fake_run), \
                mock.patch.dict("os.environ", {"CDP_BATCH_ID": "abc"},
                                clear=True):
            wu._mark_stage("verify_unit_test", dur_s=dur_s)
        return captured["args"]

    def test_dur_s_appended_as_flag(self):
        args = self._capture(dur_s=8.04)
        self.assertIn("--dur-s", args)
        self.assertEqual(args[args.index("--dur-s") + 1], "8.04")

    def test_no_dur_s_omits_flag(self):
        args = self._capture(dur_s=None)
        self.assertNotIn("--dur-s", args)


if __name__ == "__main__":
    unittest.main()

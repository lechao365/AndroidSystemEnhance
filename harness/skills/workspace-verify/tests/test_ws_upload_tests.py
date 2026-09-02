# ws_upload_tests.py 单测：设备侧单测执行环节的解析/定位/编排逻辑。
# 关键场景：yaml test_targets 读取、nativetest 二进制定位、push+执行
# 汇总 pass/fail、二进制缺失判红、设备不可达退 1。

import os
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

    def test_fail_returns_not_ok_with_output(self):
        with mock.patch.object(wu, "adb_run", side_effect=[
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
                mock.patch.object(wu, "run_one", return_value=(True, "t1: PASS")):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 0)
        ec.assert_called_once()

    def test_fail_returns_1(self):
        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user", return_value=(True, "")), \
                mock.patch.object(wu, "run_one", return_value=(False, "t1: FAIL")):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 1)

    def test_ensure_user_failure_marks_fail(self):
        with mock.patch.object(wu.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wu, "ensure_user", return_value=(False, "adb root 失败")), \
                mock.patch.object(wu, "run_one", return_value=(True, "t1: PASS")):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 1)

    def test_device_unreachable_returns_1(self):
        with mock.patch.object(wu.ac, "ensure_connected", return_value=""):
            rc = wu.main(["--test-targets", "t1"])
        self.assertEqual(rc, 1)


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


if __name__ == "__main__":
    unittest.main()

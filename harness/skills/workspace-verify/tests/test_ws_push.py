# ws_push.py 单测：推送映射解析、回读校验判红（方向 2）、生效门禁（方向 4）、
# verify_push 打点移位（方向 5）、自描述产物与退出码。
# 全程 mock adb/连接层，不触真实设备。

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_push as wp  # noqa: E402

YAML_MIN = (
    "modules:\n"
    "  lcview:\n"
    "    push:\n"
    "      - module: m1\n"
    "        dst:\n"
    "          - /vendor/bin/app1\n"
    "          - /vendor/etc/init/app1.rc\n"
    "      - module: m2\n"
    "        dst:\n"
    "          - /vendor/etc/lcview_events.json\n"
)

YAML_NO_PUSH = "modules:\n  lcview:\n    targets: [x]\n"

CTX_OK = "u:object_r:vendor_file:s0"


def make_out(root, dst):
    """在临时 out 目录按 dst 相对路径造本地产物（内容 b'A'*100）。"""
    local = Path(root) / "target" / "product" / "rpi5" / dst.lstrip("/")
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"A" * 100)
    return str(local)


class TestLoadPushMap(unittest.TestCase):
    def test_reads_all_modules_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text(YAML_MIN, encoding="utf-8")
            items, err = wp.load_push_map(str(cfg))
        self.assertIsNone(err)
        self.assertEqual(items, [
            {"module": "m1", "dst": "/vendor/bin/app1"},
            {"module": "m1", "dst": "/vendor/etc/init/app1.rc"},
            {"module": "m2", "dst": "/vendor/etc/lcview_events.json"},
        ])

    def test_modules_filter_keeps_order(self):
        # --modules 按 yaml 顶层模块段名过滤（lcview/lciod），非 push entry
        # 的 Soong module 名；段内全部 push 项保留
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text(YAML_MIN, encoding="utf-8")
            items, err = wp.load_push_map(str(cfg), {"lcview"})
        self.assertIsNone(err)
        self.assertEqual([i["dst"] for i in items],
                         ["/vendor/bin/app1", "/vendor/etc/init/app1.rc",
                          "/vendor/etc/lcview_events.json"])

    def test_unknown_module_filter_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text(YAML_MIN, encoding="utf-8")
            _, err = wp.load_push_map(str(cfg), {"no-such-module"})
        self.assertIn("无 push 映射", err)

    def test_missing_module_or_dst_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text("modules:\n  m:\n    push:\n      - dst: [/x]\n",
                           encoding="utf-8")
            _, err = wp.load_push_map(str(cfg))
        self.assertIn("缺 module 或 dst", err)

    def test_no_push_mapping_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text(YAML_NO_PUSH, encoding="utf-8")
            _, err = wp.load_push_map(str(cfg))
        self.assertIn("无 push 映射", err)

    def test_unreadable_cases_is_error(self):
        _, err = wp.load_push_map("/nonexistent/verify-cases.yaml")
        self.assertIn("读取失败", err)


class TestResolveSource(unittest.TestCase):
    def test_resolves_out_relative_path(self):
        with tempfile.TemporaryDirectory() as d:
            src = make_out(d, "/vendor/bin/app1")
            self.assertEqual(wp.resolve_source(d, "rpi5", "/vendor/bin/app1"), src)

    def test_missing_product_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(wp.resolve_source(d, "rpi5", "/vendor/bin/app1"))


class TestVerifyItemDirection2(unittest.TestCase):
    """方向 2：回读三项与本地产物比对，任一不符判红。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.src = make_out(self._tmp.name, "/vendor/bin/app1")
        self.local_sha = hashlib.sha256(b"A" * 100).hexdigest()

    def tearDown(self):
        self._tmp.cleanup()

    def _device(self, sha=None, nbytes=100, ctx=CTX_OK):
        return {"sha256": sha or self.local_sha, "bytes": nbytes,
                "context": ctx}

    def test_all_match_passes(self):
        ok, checks, detail, local = wp.verify_item(
            self.src, self._device())
        self.assertTrue(ok)
        self.assertEqual(checks, {"sha256": "pass", "bytes": "pass",
                                  "context": "pass"})
        self.assertEqual(local["bytes"], 100)

    def test_sha_mismatch_is_red(self):
        ok, checks, detail, _ = wp.verify_item(
            self.src, self._device(sha="0" * 64))
        self.assertFalse(ok)
        self.assertEqual(checks["sha256"], "fail")
        self.assertIn("SHA256 不符", detail)

    def test_bytes_mismatch_is_red(self):
        ok, checks, detail, _ = wp.verify_item(
            self.src, self._device(nbytes=99))
        self.assertFalse(ok)
        self.assertEqual(checks["bytes"], "fail")
        self.assertIn("字节数不符", detail)

    def test_unlabeled_context_is_red(self):
        ok, checks, detail, _ = wp.verify_item(
            self.src, self._device(ctx="u:object_r:unlabeled:s0"))
        self.assertFalse(ok)
        self.assertEqual(checks["context"], "fail")
        self.assertIn("unlabeled", detail)

    def test_empty_context_is_red(self):
        ok, checks, _, _ = wp.verify_item(self.src, self._device(ctx=None))
        self.assertFalse(ok)
        self.assertEqual(checks["context"], "fail")

    def test_nonstandard_context_is_red(self):
        ok, checks, detail, _ = wp.verify_item(
            self.src, self._device(ctx="some_random_label"))
        self.assertFalse(ok)
        self.assertEqual(checks["context"], "fail")
        self.assertIn("非标准形态", detail)

    def test_expect_context_mismatch_is_red(self):
        ok, checks, detail, _ = wp.verify_item(
            self.src, self._device(), expect_ctx="u:object_r:x:s0")
        self.assertFalse(ok)
        self.assertEqual(checks["context"], "fail")
        self.assertIn("期望不符", detail)

    def test_expect_context_match_passes(self):
        ok, checks, _, _ = wp.verify_item(
            self.src, self._device(), expect_ctx=CTX_OK)
        self.assertTrue(ok)


class TestReadbackDevice(unittest.TestCase):
    def test_parses_three_fields(self):
        def fake(ep, args, timeout=600):
            if args[0] == "shell":
                if args[1].startswith("sha256sum"):
                    return ("ab" * 32 + "  /vendor/bin/app1", 0)
                if args[1].startswith("stat"):
                    return ("100", 0)
                if args[1].startswith("ls"):
                    return (f"{CTX_OK} /vendor/bin/app1", 0)
            return ("", -1)
        with mock.patch.object(wp, "adb_run", side_effect=fake):
            dev = wp.readback_device("ep", "/vendor/bin/app1")
        self.assertEqual(dev, {"sha256": "ab" * 32, "bytes": 100,
                               "context": CTX_OK})

    def test_exec_failure_keeps_fields_none(self):
        with mock.patch.object(wp, "adb_run", return_value=("", -1)):
            dev = wp.readback_device("ep", "/vendor/bin/app1")
        self.assertIsNone(dev["sha256"])
        self.assertIsNone(dev["bytes"])
        self.assertIsNone(dev["context"])


class TestNeedsRebootDirection4(unittest.TestCase):
    """方向 4：sepolicy / vintf / rc 命中即生效类须重启。"""

    def test_rc_hits(self):
        self.assertTrue(wp.needs_reboot("/vendor/etc/init/lechao_lcview.rc"))
        self.assertTrue(wp.needs_reboot("/system/etc/init/lechao_lciod.rc"))

    def test_selinux_hits(self):
        self.assertTrue(wp.needs_reboot(
            "/vendor/etc/selinux/precompiled_sepolicy"))
        self.assertTrue(wp.needs_reboot(
            "/vendor/etc/selinux/vendor_file_contexts"))

    def test_vintf_hits(self):
        self.assertTrue(wp.needs_reboot(
            "/vendor/etc/vintf/manifest/vendor.lechao.lciod.IIoHal-service.xml"))

    def test_plain_binaries_do_not_hit(self):
        self.assertFalse(wp.needs_reboot("/vendor/bin/lechao_lcview"))
        self.assertFalse(wp.needs_reboot("/system/bin/lciod_probe"))
        self.assertFalse(wp.needs_reboot("/vendor/lib64/libjsoncpp.so"))
        self.assertFalse(wp.needs_reboot("/vendor/etc/lcview_events.json"))


class TestRebootAndWaitDirection4(unittest.TestCase):
    """方向 4：重启后等启动完成；未就绪/不可达超时判红。

    方向 1：wp._sleep（实时等待注入点）全部 patch——reboot settle 8s 与
    轮询间隔 5s 曾让本类 3 用例实跑 13.0/13.0/8.0s，每次自检多花 15~28s
    且随 xdist 分发波动。
    """

    def test_recovers_and_ready_passes(self):
        with mock.patch.object(wp, "_sleep") as sl, \
                mock.patch.object(wp.ac, "ensure_connected",
                                  side_effect=[None, "ep2"]) as ec, \
                mock.patch.object(wp.ac, "ensure_ready",
                                  return_value=True) as er:
            ok, detail = wp.reboot_and_wait("ep")
        self.assertTrue(ok)
        self.assertIn("启动完成", detail)
        ec.assert_called()
        er.assert_called()
        # 注入点生效：settle 8s 经 _sleep 下发（无真实等待）
        self.assertIn(mock.call(8), sl.call_args_list)

    def test_never_ready_is_red(self):
        with mock.patch.object(wp, "_sleep"), \
                mock.patch.object(wp.ac, "ensure_connected",
                                  return_value=None), \
                mock.patch.object(wp.ac, "ensure_ready", return_value=False):
            ok, detail = wp.reboot_and_wait("ep", boot_timeout=1)
        self.assertFalse(ok)
        self.assertIn("超时", detail)

    def test_reboot_executed_via_adb(self):
        # 重启动作必须真实下发 adb reboot（跳过由门禁语义杜绝，见
        # TestMainOrchestration.test_no_skip_reboot_flag）
        with mock.patch.object(wp, "_sleep"), \
                mock.patch.object(wp, "adb_run", return_value=("", 0)) as ar, \
                mock.patch.object(wp.ac, "ensure_connected",
                                  return_value="ep"), \
                mock.patch.object(wp.ac, "ensure_ready", return_value=True):
            wp.reboot_and_wait("ep")
        self.assertEqual(ar.call_args.args[1], ["reboot"])

    def test_sleep_injection_point_covers_settle_and_poll(self):
        # 方向 1：等待全部经 _sleep 注入点（settle 8s + 轮询 5s），
        # patch 后用例瞬时完成且可断言等待参数（无 time.sleep 直调残留）
        seen = []
        with mock.patch.object(wp, "_sleep", side_effect=seen.append), \
                mock.patch.object(wp.ac, "ensure_connected",
                                  return_value=None):
            wp.reboot_and_wait("ep", boot_timeout=1)
        self.assertIn(8, seen)
        self.assertIn(5, seen)


class TestMainOrchestration(unittest.TestCase):
    """main 编排：映射推送 + 回读判红 + 生效门禁 + 打点移位 + 退出码。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = self._tmp.name
        self.cfg = Path(self.out) / "verify-cases.yaml"
        self.cfg.write_text(YAML_MIN, encoding="utf-8")
        self.result = Path(self.out) / "push-result.json"
        self.events = []

    def tearDown(self):
        self._tmp.cleanup()

    def _local_sha(self, dst):
        p = Path(self.out) / "target" / "product" / "rpi5" / dst.lstrip("/")
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def _run(self, extra=None, dev_sha=None, dev_bytes=None, dev_ctx=CTX_OK,
             reboot_ok=True, create=True):
        dsts = ("/vendor/bin/app1", "/vendor/etc/init/app1.rc",
                "/vendor/etc/lcview_events.json")
        if create:
            for dst in dsts:
                make_out(self.out, dst)
        shas = {dst: self._local_sha(dst) for dst in dsts if create}
        # 设备侧按 dst 返回真实/伪造指纹（None → 用本地真值 = 全绿）
        def fake(ep, args, timeout=600):
            if args[0] == "push":
                self.events.append(("push", args[2]))
                return ("", 0)
            if args[0] == "shell":
                cmd = args[1]
                dst = cmd.split()[-1] if len(cmd.split()) > 1 else ""
                if cmd.startswith("sha256sum"):
                    return (f"{dev_sha or shas.get(dst, '0' * 64)}  x", 0)
                if cmd.startswith("stat"):
                    return (str(dev_bytes if dev_bytes is not None else 100), 0)
                if cmd.startswith("ls"):
                    return (f"{dev_ctx} /x", 0)
            return ("", 0)
        argv = ["--out", self.out, "--cases", str(self.cfg),
                "--result-file", str(self.result)] + (extra or [])
        with mock.patch.object(wp.ac, "ensure_connected", return_value="ep"), \
                mock.patch.object(wp, "ensure_root_remount",
                                  return_value=(True, "")), \
                mock.patch.object(wp, "adb_run", side_effect=fake), \
                mock.patch.object(wp, "reboot_and_wait",
                                  return_value=(reboot_ok, "启动完成")) as rb, \
                mock.patch.object(wp, "_mark_stage",
                                  side_effect=lambda n: self.events.append(
                                      ("mark", n))):
            rc = wp.main(argv)
        return rc, rb

    def test_all_green_with_rc_hits_reboot(self):
        # rc 命中（/vendor/etc/init/app1.rc）→ 强制重启；全绿 → rc 0
        rc, rb = self._run()
        self.assertEqual(rc, 0)
        rb.assert_called_once()
        data = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(data["overall"], "pass")
        self.assertTrue(data["reboot"]["required"])
        self.assertEqual(len(data["items"]), 3)
        for it in data["items"]:
            self.assertEqual(it["checks"],
                             {"sha256": "pass", "bytes": "pass",
                              "context": "pass"})
            self.assertTrue(it["source"])
        self.assertTrue(data["run_id"])

    def test_verify_push_marked_after_all_pushes_direction5(self):
        # 方向 5：verify_push 打点在实际推送循环完成后——mark 事件晚于
        # 全部 push 事件，且先于生效门禁重启
        rc, _ = self._run()
        self.assertEqual(rc, 0)
        marks = [i for i, e in enumerate(self.events) if e[0] == "mark"]
        pushes = [i for i, e in enumerate(self.events) if e[0] == "push"]
        boots = [i for i, e in enumerate(self.events) if e[0] == "reboot"]
        self.assertEqual([self.events[i] for i in marks],
                         [("mark", "verify_push")])
        self.assertTrue(marks[0] > max(pushes))
        if boots:
            self.assertTrue(marks[0] < min(boots))

    def test_sha_mismatch_is_red_direction2(self):
        # 方向 2：设备侧 SHA256 与本地产物不符 → 判红 exit 1，产物记 fail
        rc, _ = self._run(dev_sha="f" * 64)
        self.assertEqual(rc, 1)
        data = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(data["overall"], "fail")
        self.assertTrue(all(it["checks"]["sha256"] == "fail"
                            for it in data["items"]))

    def test_bytes_mismatch_is_red_direction2(self):
        rc, _ = self._run(dev_bytes=99)
        self.assertEqual(rc, 1)
        data = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(data["overall"], "fail")
        self.assertTrue(all(it["checks"]["bytes"] == "fail"
                            for it in data["items"]))

    def test_unlabeled_context_is_red_direction2(self):
        rc, _ = self._run(dev_ctx="u:object_r:unlabeled:s0")
        self.assertEqual(rc, 1)
        data = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(data["overall"], "fail")

    def test_reboot_failure_is_red_direction4(self):
        # 方向 4：命中生效项但重启后未就绪 → 判红，产物 reboot.ok=False
        rc, rb = self._run(reboot_ok=False)
        self.assertEqual(rc, 1)
        rb.assert_called_once()
        data = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(data["overall"], "fail")
        self.assertFalse(data["reboot"]["ok"])

    def test_missing_product_is_red(self):
        # 编译产物缺失不能当通过：判红且该项 source=None
        rc, _ = self._run(create=False)
        self.assertEqual(rc, 1)
        data = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertTrue(any(it["source"] is None for it in data["items"]))

    def test_no_push_mapping_exits_2(self):
        cfg = Path(self.out) / "nopush.yaml"
        cfg.write_text(YAML_NO_PUSH, encoding="utf-8")
        with mock.patch.object(wp.ac, "ensure_connected", return_value="ep"):
            rc = wp.main(["--out", self.out, "--cases", str(cfg)])
        self.assertEqual(rc, 2)

    def test_expect_context_mismatch_is_red(self):
        rc, _ = self._run(extra=["--expect-context",
                                 "/vendor/bin/app1=u:object_r:other:s0"])
        self.assertEqual(rc, 1)

    def test_no_skip_reboot_flag(self):
        # 方向 4"跳过即判红"：接口层不存在跳过开关，传 --no-reboot 直接
        # 参数错误（SystemExit），杜绝用参数绕过生效门禁
        with self.assertRaises(SystemExit):
            wp.main(["--no-reboot"])


class TestAtomicWrite(unittest.TestCase):
    def test_no_tmp_leftover_and_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "r.json"
            wp._atomic_write_json(p, {"a": 1})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1})
            self.assertEqual(list(Path(d).rglob("*.tmp")), [])

    def test_expect_context_parse(self):
        self.assertEqual(
            wp._parse_expect_context("/a=u:object_r:x:s0, /b=u:object_r:y:s0"),
            {"/a": "u:object_r:x:s0", "/b": "u:object_r:y:s0"})
        with self.assertRaises(ValueError):
            wp._parse_expect_context("/a-no-eq")


if __name__ == "__main__":
    unittest.main()

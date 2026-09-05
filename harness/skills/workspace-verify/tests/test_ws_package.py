# ws_package 单测：打包证据生产（mk_rpi5_full_image.sh mode 0 包装）。
# 关键场景：三镜像齐备真跑（打桩 subprocess）→ 证据含镜像路径/sha256/字节/
# rc/耗时且原子写；镜像缺失 → 如实记因不执行不产假证据；BLD-007 sudo 行
# 未显式传参 → 拒执行；成功后登记最新 SD 卡刷机镜像。
# 注：AOSP_WS/脚本路径/证据目录均 patch 到临时域，subprocess 打桩隔离。

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_package as wp


class TestWsPackage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self.aosp = Path(self._tmp.name) / "aosp"
        self.product_out = self.aosp / "out" / "target" / "product" / "rpi5"
        self.product_out.mkdir(parents=True)
        self.script = Path(self._tmp.name) / "mk_rpi5_full_image.sh"
        # 含 BLD-007 合规 sudo 行的假脚本（正则与真脚本同形）
        self.script.write_text(
            'if ! sudo TARGET_PRODUCT="${TARGET_PRODUCT}" '
            'ANDROID_PRODUCT_OUT="${ANDROID_PRODUCT_OUT}" ./rpi5-mkimg.sh; then\n',
            encoding="utf-8")
        self.evidence = Path(self._tmp.name) / "package-x.json"
        self._imgs = {}
        for name in wp._IMAGES:
            p = self.product_out / name
            p.write_bytes(b"\x00" * 16)
            self._imgs[name] = p

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT", None)
        os.environ.pop("CDP_BATCH_ID", None)
        os.environ.pop("CDP_RUN_ID", None)

    def _run(self, run=True, script_rc=0, sudo_rc=0, **kw):
        kw.setdefault("aosp_ws", str(self.aosp))
        if "evidence_file" in kw and kw["evidence_file"] is None:
            kw.pop("evidence_file")  # 显式 None：交给 run_package 缺省命名
        else:
            kw.setdefault("evidence_file", str(self.evidence))

        def _fake(args, *a, **k):
            # 两次调用区分：sudo -n true 探测 vs 打包脚本执行（方向 4）
            if list(args) == ["sudo", "-n", "true"]:
                return mock.Mock(returncode=sudo_rc, stdout="", stderr="")
            return mock.Mock(returncode=script_rc, stdout="ok", stderr="")

        with mock.patch.object(wp, "_SCRIPT", self.script), \
                mock.patch.object(wp.subprocess, "run",
                                  side_effect=_fake) as run_mock:
            rc, ev = wp.run_package(run=run, **kw)
        return rc, ev, run_mock

    def test_success_evidence_complete(self):
        # 真跑成功：证据含三镜像路径/sha256/字节 + rc + 耗时 + SD 镜像，原子落盘
        img = self.product_out / "RaspberryVanillaAOSP15-20260905-rpi5.img"
        img.write_bytes(b"\x01" * 8)
        rc, ev, run_mock = self._run(script_rc=0)
        self.assertEqual(rc, 0)
        self.assertEqual(ev["script_rc"], 0)
        self.assertTrue(ev["ran"])
        self.assertTrue(ev["images_ok"])
        self.assertTrue(ev["sudo_bld007"])
        self.assertEqual(len(ev["images"]), 3)
        for entry in ev["images"]:
            self.assertEqual(entry["bytes"], 16)
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertTrue(Path(entry["path"]).is_file())
        self.assertEqual(ev["packaged_img"]["bytes"], 8)
        self.assertIn("dur_s", ev)
        self.assertIn("started_at", ev)
        # 原子写落盘且子进程以显式 env 执行（BLD-007 调用方侧）
        data = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertEqual(data["script_rc"], 0)
        env = run_mock.call_args.kwargs["env"]
        self.assertEqual(env["TARGET_PRODUCT"], "aosp_rpi5")
        self.assertEqual(env["ANDROID_PRODUCT_OUT"], str(self.product_out))
        self.assertEqual(run_mock.call_args.args[0],
                         ["bash", str(self.script), "-mode", "0"])

    def test_missing_image_records_reason_no_fake_evidence(self):
        # 镜像缺失：不执行打包，证据如实记原因（rc 非 0），不产假证据
        (self.product_out / "vendor.img").unlink()
        rc, ev, run_mock = self._run()
        self.assertEqual(rc, 1)
        self.assertFalse(ev["ran"])
        self.assertIsNone(ev["script_rc"])
        self.assertFalse(ev["images_ok"])
        self.assertIn("vendor.img", ev["error"])
        self.assertIn("镜像缺失", ev["error"])
        run_mock.assert_not_called()
        data = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertIsNone(data["script_rc"])

    def test_bld007_violation_refuses(self):
        # sudo 行未显式传参（如 -E/裸 sudo）：拒执行并如实记因
        self.script.write_text("if ! sudo -E ./rpi5-mkimg.sh; then\n",
                               encoding="utf-8")
        rc, ev, run_mock = self._run()
        self.assertEqual(rc, 1)
        self.assertFalse(ev["sudo_bld007"])
        self.assertIn("BLD-007", ev["error"])
        run_mock.assert_not_called()

    def test_script_failure_recorded(self):
        # 打包脚本 rc 非 0：证据如实记 rc 与 output_tail，整体 rc 1
        rc, ev, _ = self._run(script_rc=3)
        self.assertEqual(rc, 1)
        self.assertEqual(ev["script_rc"], 3)
        self.assertIn("rc=3", ev["error"])
        self.assertIn("output_tail", ev)

    def test_sudo_unavailable_refuses(self):
        # 方向 4：sudo -n true 探测失败（需密码/无权限）→ 如实记因不执行，
        # 打包脚本不被调用（防非 tty 卡死或错报）
        rc, ev, run_mock = self._run(sudo_rc=1)
        self.assertEqual(rc, 1)
        self.assertFalse(ev["sudo_n"])
        self.assertFalse(ev["ran"])
        self.assertIsNone(ev["script_rc"])
        self.assertIn("sudo", ev["error"])
        self.assertIn("拒绝执行", ev["error"])
        # 仅探测调用（sudo），打包脚本（bash <script>）未被调用
        scripts = [c for c in run_mock.call_args_list
                   if list(c.args[0])[:1] == ["bash"]]
        self.assertEqual(scripts, [])
        data = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertFalse(data["sudo_n"])
        self.assertIn("sudo", data["error"])

    def test_sudo_probe_success_proceeds(self):
        # sudo 探测通过（sudo_rc=0）→ 正常走打包，证据 sudo_n=True
        img = self.product_out / "RaspberryVanillaAOSP15-20260905-rpi5.img"
        img.write_bytes(b"\x01" * 8)
        rc, ev, _ = self._run(sudo_rc=0, script_rc=0)
        self.assertEqual(rc, 0)
        self.assertTrue(ev["sudo_n"])
        self.assertEqual(ev["script_rc"], 0)

    def test_missing_aosp_ws(self):
        # AOSP_WS 未配置（paths.env_path 返空）：如实记因不执行
        with mock.patch.object(wp.paths, "env_path", return_value=""):
            rc, ev = wp.run_package(run=False,
                                    evidence_file=str(self.evidence))
        self.assertEqual(rc, 1)
        self.assertIn("AOSP_WS", ev["error"])

    def test_batch_id_from_env_names_evidence(self):
        # CDP_BATCH_ID env：缺省证据文件名取 batch_id（baseline_register 探测同名）
        os.environ["CDP_BATCH_ID"] = "ff33f92060ac"
        rc, ev, _ = self._run(run=False, evidence_file=None)
        self.assertEqual(rc, 0)
        self.assertEqual(ev["batch_id"], "ff33f92060ac")
        self.assertTrue(ev["evidence_file"].endswith(
            "package-ff33f92060ac.json"))


if __name__ == "__main__":
    unittest.main()

# ws_package 单测：打包证据生产（mk_rpi5_full_image.sh mode 0 包装）。
# 关键场景：三镜像齐备真跑（打桩 privileged_exec helper）→ 证据含镜像
# 路径/sha256/字节/rc/耗时/privilege 且原子写；镜像缺失 → 如实记因不执行
# 不产假证据；BLD-007 sudo 行未显式传参 → 拒执行；helper 拒绝/审计失败
# 的归因透传；成功后登记最新 SD 卡刷机镜像。
# 注：AOSP_WS/脚本路径/证据目录 patch 到临时域，helper probe/run 打桩隔离。

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
        self.evidence_dir = Path(self._tmp.name) / "evidence"
        self.evidence_dir.mkdir()
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

    def _run(self, run=True, script_rc=0, probe=None, result=None,
             run_error=None, **kw):
        kw.setdefault("aosp_ws", str(self.aosp))
        if "evidence_file" in kw and kw["evidence_file"] is None:
            kw.pop("evidence_file")  # 显式 None：交给 run_package 缺省命名
        else:
            kw.setdefault("evidence_file", str(self.evidence))
        if probe is None:
            probe = wp.priv.ProbeResult(
                True, False, True, "direct", True, "")
        if result is None:
            result = wp.priv.RunResult(
                script_rc, "package.rpi5.mode0", "direct", True, False,
                True, 0.1, "", "ok", "" if script_rc == 0 else "rc")
        run_kwargs = {"side_effect": run_error} if run_error is not None \
            else {"return_value": result}
        with mock.patch.object(wp, "_SCRIPT", self.script), \
                mock.patch.object(wp, "_EVIDENCE_DIR", self.evidence_dir), \
                mock.patch.object(wp.priv, "probe", return_value=probe), \
                mock.patch.object(wp.priv, "run", **run_kwargs) as run_mock:
            rc, ev = wp.run_package(run=run, **kw)
        return rc, ev, run_mock

    def test_success_evidence_complete(self):
        # 真跑成功：证据含三镜像 + rc + 耗时 + SD 镜像，且记录 privilege 段
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
        self.assertTrue(ev["privilege"]["usable"])
        self.assertEqual(ev["privilege"]["mechanism"], "direct")
        self.assertEqual(ev["privilege"]["rejected_code"], "")
        self.assertEqual(ev["privilege"]["audit_error"], "")
        run_mock.assert_called_once()
        call = run_mock.call_args
        self.assertEqual(call.args[0], "package.rpi5.mode0")
        self.assertEqual(call.args[1],
                         ["bash", str(self.script), "-mode", "0"])
        self.assertEqual(call.kwargs["reason"], "ws_package mode 0")
        self.assertEqual(call.kwargs["timeout"], 900)
        self.assertEqual(call.kwargs["cwd"],
                         str(wp._SCRIPT_DIR.parents[2]))
        self.assertEqual(call.kwargs["env"]["TARGET_PRODUCT"], "aosp_rpi5")
        self.assertEqual(call.kwargs["env"]["ANDROID_PRODUCT_OUT"],
                         str(self.product_out))
        data = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertEqual(data["script_rc"], 0)

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
        # helper 判定提权不可用（direct sudo 与 systemd-run 均失败）→
        # 如实记因不执行，错误指向 privileged_exec --check 与 allowlist
        probe = wp.priv.ProbeResult(
            False, True, False, "none", False, "no systemd")
        rc, ev, run_mock = self._run(probe=probe)
        self.assertEqual(rc, 1)
        self.assertFalse(ev["sudo_n"])
        self.assertFalse(ev["privilege"]["usable"])
        self.assertIn("privileged_exec.py --check", ev["error"])
        self.assertIn("allowlist", ev["error"])
        run_mock.assert_not_called()

    def test_systemd_run_success(self):
        # NNP 会话下 systemd-run 成功：sudo_n=false 但 context_sudo_ok=true
        probe = wp.priv.ProbeResult(
            False, True, True, "systemd-run", True, "systemd-run 可用")
        result = wp.priv.RunResult(
            0, "package.rpi5.mode0", "systemd-run", False, True, True,
            0.1, "audit.json", "ok", "")
        img = self.product_out / "RaspberryVanillaAOSP15-20260905-rpi5.img"
        img.write_bytes(b"\x01" * 8)
        rc, ev, _ = self._run(probe=probe, result=result)
        self.assertEqual(rc, 0)
        self.assertEqual(ev["script_rc"], 0)
        self.assertTrue(ev["ran"])
        self.assertEqual(ev["output_tail"], "ok")
        self.assertFalse(ev["sudo_n"])
        self.assertEqual(ev["privilege"]["mechanism"], "systemd-run")
        self.assertTrue(ev["privilege"]["context_sudo_ok"])
        self.assertEqual(ev["privilege"]["audit"], "audit.json")
        self.assertEqual(ev["privilege"]["audit_file"], "audit.json")

    def test_helper_rejection_recorded(self):
        # probe 可用但 helper 拒绝（如 AOSP_WS 非默认未同步 trusted_roots）：
        # 如实记录拒绝 code，不执行、不产假证据
        err = wp.priv.PrivilegeError(
            "PRIV_ENV_PATH_DENIED", "PATH 指向非受控根")
        rc, ev, run_mock = self._run(run_error=err)
        self.assertEqual(rc, 1)
        self.assertFalse(ev["ran"])
        self.assertIsNone(ev["script_rc"])
        self.assertIn("PRIV_ENV_PATH_DENIED", ev["error"])
        self.assertEqual(ev["privilege"]["rejected_code"],
                         "PRIV_ENV_PATH_DENIED")
        run_mock.assert_called_once()

    def test_audit_failure_not_silenced(self):
        # helper 执行成功但审计写失败：rc/PASS 不变，审计错误单独留痕
        result = wp.priv.RunResult(
            0, "package.rpi5.mode0", "direct", True, False, True,
            0.1, "", "ok", "PRIV_AUDIT_WRITE_FAILED: disk")
        img = self.product_out / "RaspberryVanillaAOSP15-20260905-rpi5.img"
        img.write_bytes(b"\x01" * 8)
        rc, ev, _ = self._run(result=result)
        self.assertEqual(rc, 0)
        self.assertEqual(ev["error"], "")
        self.assertIn("PRIV_AUDIT_WRITE_FAILED",
                      ev["privilege"]["audit_error"])

    def test_missing_aosp_ws(self):
        # AOSP_WS 未配置（paths.env_path 返空）：如实记因不执行
        with mock.patch.object(wp.paths, "env_path", return_value=""):
            rc, ev = wp.run_package(run=False,
                                    evidence_file=str(self.evidence))
        self.assertEqual(rc, 1)
        self.assertIn("AOSP_WS", ev["error"])

    def test_resolve_batch_id_narrowed_two_level(self):
        # 方向 2：batch_id 回落收窄两级（CDP_BATCH_ID > current-batch.json），
        # 去掉唯一 timings 回落——手工跑（无批上下文）打点目录残留不再误绑
        # 当批（与 ws_acceptance 同源收窄，防打包证据落错批次 id）
        logdir = Path(self._tmp.name) / "harness" / "log" / "cross-device"
        logdir.mkdir(parents=True)
        (logdir / "current-batch.json").write_text(
            json.dumps({"batch_id": "fedcba654321"}), encoding="utf-8")
        self.assertEqual(wp._resolve_batch_id(), "fedcba654321")
        # 无 current-batch 指针：唯一/多个 timings 文件均不再回落（两级收窄）
        (logdir / "current-batch.json").unlink()
        for bid in ("aaa111bbb222", "ccc333ddd444"):
            (logdir / f"timings-{bid}.json").write_text("{}",
                                                        encoding="utf-8")
        self.assertIsNone(wp._resolve_batch_id())

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

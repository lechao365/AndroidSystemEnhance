import contextlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_report

VALID_S = """-s base:1a2b3c4d5e6f
意图: 更新 README 映射表说明
验收: 无
方向: 补充新增文件条目描述
"""


class TestWsReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._dir = Path(self._tmp.name) / "data" / "verify"

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def _write(self, content, suffix=".txt"):
        f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                        encoding="utf-8")
        f.write(content)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_mode_a_normal(self):
        # 模式 A：--batch-file + --body → exit 0 且收据落盘、body 内容写入
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\nadb 失败\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s 说明"])
        self.assertEqual(rc, 0)
        self.assertIn("receipt:", buf.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("batch_id: ", content)
        self.assertIn("## body", content)
        self.assertIn("adb 失败", content)

    def test_mode_mutex_both_missing(self):
        # --batch-file 与 --target 皆缺 → exit 2，不落盘
        rc = ws_report.main(["--result", "skip"])
        self.assertEqual(rc, 2)
        self.assertFalse(self._dir.exists())

    def test_mode_a_body_missing_file(self):
        # 模式 A 下 --body 文件不存在 → exit 2，不落盘
        batch = self._write(VALID_S, ".cdp")
        rc = ws_report.main(["--batch-file", batch, "--body",
                             "/nonexistent/body.txt", "--result", "skip"])
        self.assertEqual(rc, 2)
        self.assertFalse(self._dir.exists())

    def test_mode_a_flattened_batch_returns_2(self):
        # 模式 A 下批次原文被压平（echo 类写法致多行并成一行、base 丢失）
        # → validate_batch 校验失败 exit 2，报错到 stderr，不落盘
        flat = self._write(
            "-s base:1a2b3c4d5e6f 意图: 更新 README 映射表说明 验收: 无 方向: 补充条目\n",
            ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with contextlib.redirect_stderr(err):
                rc = ws_report.main(["--batch-file", flat, "--body", body,
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "s"])
        self.assertEqual(rc, 2)
        self.assertIn("error: 批次校验失败", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_mode_a_soft_error_degrades_to_warn(self):
        # apply 角色下 SOFT_ERRORS（-sv 批次验收为「无」→ 17 验收规则违规）
        # 仅 warn 不 return 2，收据仍落盘（与 cdp_parse 降级语义一致）
        soft = self._write(
            "-sv base:1a2b3c4d5e6f\n"
            "意图: 触发验收规则违规降级路径\n"
            "验收: 无\n"
            "方向: 验证 apply 角色下 17 降级为 warn 不阻断\n",
            ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with contextlib.redirect_stderr(err):
                rc = ws_report.main(["--batch-file", soft, "--body", body,
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "sv 降级",
                                     "--acceptance", "svc:x running"])
        self.assertEqual(rc, 0)
        self.assertIn("warn: 批次校验失败", err.getvalue())
        self.assertNotIn("error: 批次校验失败", err.getvalue())
        self.assertIn("receipt:", buf.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)

    def test_mode_a_sv_requires_acceptance(self):
        # 模式 A -sv 批次必须传 --acceptance，否则返 2 拒写收据（baseline 证据链防洞）
        sv = self._write(
            "-sv base:1a2b3c4d5e6f\n"
            "意图: 上板验证 lcview\n"
            "验收: svc:lechao_lcview\n"
            "方向: 检查 service 运行\n",
            ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", sv, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "sv 无证据"])
        self.assertEqual(rc, 2)
        self.assertIn("必须传 --acceptance", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_mode_b_board_skip_verify_mode_none(self):
        # 模式 B：--board skip（revert 恢复验证未上板）→ verify_mode=none
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--target", "1a2b3c4d5e6f",
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "revert 恢复"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- verify_mode: none", content)

    def test_mode_b_board_pass_verify_mode_board(self):
        # 模式 B：--board pass（真上板验证）→ verify_mode=board
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--target", "1a2b3c4d5e6f",
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "上板通过"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- verify_mode: board", content)

    def test_sanitize_workspace_placeholder(self):
        # KERNEL_WS/AOSP_WS 绝对路径 → <KEY> 占位符，且优先于家目录正则
        with mock.patch.object(ws_report, "env_path",
                               side_effect=lambda k, d=None: {
                                   "KERNEL_WS": "/home/u/ws/kernel",
                                   "AOSP_WS": "/home/u/ws/aosp"}.get(k, "")):
            out = ws_report._sanitize(
                "编译 /home/u/ws/kernel/out 失败，镜像 /home/u/ws/aosp/out/aosp.img")
        self.assertIn("<KERNEL_WS>/out", out)
        self.assertIn("<AOSP_WS>/out/aosp.img", out)
        self.assertNotIn("/home/u/ws", out)

    def test_sanitize_home_only(self):
        # 无 workspace 路径时家目录绝对路径 → ~
        with mock.patch.object(ws_report, "env_path", return_value=""):
            out = ws_report._sanitize("/home/lechao/foo 与 /home/other/bar")
        self.assertEqual(out, "~/foo 与 ~/bar")

    def test_resolve_target_12hex_passthrough(self):
        # 12hex 原样返回且无错误，不触发 git 调用
        val, err = ws_report._resolve_target("1a2b3c4d5e6f")
        self.assertEqual(val, "1a2b3c4d5e6f")
        self.assertIsNone(err)

    def test_resolve_target_dev_via_git(self):
        # dev 等描述经 git rev-parse --short=12 换算为 12hex（promote 门禁比对 HEAD^）
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "aabbccddeeff\n"
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake) as m:
            val, err = ws_report._resolve_target("dev")
        self.assertEqual(val, "aabbccddeeff")
        self.assertIsNone(err)
        self.assertEqual(m.call_args.args[0],
                         ["git", "rev-parse", "--short=12", "dev"])

    def test_resolve_target_git_failure_rejects(self):
        # git 不可用（OSError）或引用不存在（rc!=0）→ 返回 err 供调用方拒写，
        # 不再写空串蒙混（空 verified_commit 致 promote 门禁比对不等空串）
        with mock.patch.object(ws_report.subprocess, "run",
                               side_effect=OSError("no git")):
            val, err = ws_report._resolve_target("dev")
        self.assertEqual(val, "")
        self.assertIn("无法解析", err)
        fake = mock.Mock()
        fake.returncode = 128
        fake.stdout = ""
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            val, err = ws_report._resolve_target("nope")
        self.assertEqual(val, "")
        self.assertIn("退出 128", err)

    def test_mode_b_target_dev_resolved_to_commit(self):
        # 模式 B --target dev：收据 verified_commit 为 rev-parse 结果（非字面量 dev）
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "aabbccddeeff\n"
        buf = io.StringIO()
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            with redirect_stdout(buf):
                rc = ws_report.main(["--target", "dev",
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "dev 描述"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("aabbccddeeff", content)
        self.assertNotIn("- verified_commit: dev", content)

    def test_mode_b_target_unresolvable_rejects(self):
        # 模式 B --target 解析失败（git 引用不存在）→ exit 2 拒写收据
        fake = mock.Mock()
        fake.returncode = 128
        fake.stdout = ""
        err = io.StringIO()
        buf = io.StringIO()
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            with redirect_stdout(buf):
                with contextlib.redirect_stderr(err):
                    rc = ws_report.main(["--target", "nope",
                                         "--result", "skip", "--build", "skip",
                                         "--board", "skip", "--summary", "坏 target"])
        self.assertEqual(rc, 2)
        self.assertIn("无法解析 --target", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_mode_a_target_dev_resolved_to_commit(self):
        # 模式 A 显式 --target dev 同走 _resolve_target（模式 B 之外的遗漏覆盖）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "aabbccddeeff\n"
        buf = io.StringIO()
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            with redirect_stdout(buf):
                rc = ws_report.main(["--batch-file", batch, "--body", body,
                                     "--target", "dev",
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "A dev target"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("aabbccddeeff", content)
        self.assertNotIn("- verified_commit: dev", content)


if __name__ == "__main__":
    unittest.main()

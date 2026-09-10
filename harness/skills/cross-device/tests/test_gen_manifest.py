import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import gen_manifest as gm


class TestGenManifest(unittest.TestCase):
    def _make_patch_root(self, files):
        d = Path(tempfile.mkdtemp())
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return d

    def test_check_only_no_write_when_absent(self):
        # --check-only：manifest 不存在时不写盘
        root = self._make_patch_root({"aosp/new/vendor/x/foo.h": "//x"})
        gm.generate_manifest(root, check_only=True,
                             kernel_deletions=[], aosp_deletions=[])
        self.assertFalse((root / "manifest.yaml").exists())

    def test_gen_writes_entries_with_source_map(self):
        # 正常生成：patch 相对路径 + source 映射（aosp/kernel 前缀换算）
        root = self._make_patch_root({
            "aosp/new/vendor/x/foo.h": "//x",
            "kernel/new/drivers/y.c": "//y",
        })
        gm.generate_manifest(root, check_only=False,
                             kernel_deletions=[], aosp_deletions=[])
        content = (root / "manifest.yaml").read_text(encoding="utf-8")
        self.assertIn("patch: aosp/new/vendor/x/foo.h", content)
        self.assertIn("source: aosp/vendor/x/foo.h", content)
        self.assertIn("patch: kernel/new/drivers/y.c", content)
        self.assertIn("source: rpi5-kernel-build/common/drivers/y.c", content)

    def test_check_only_keeps_existing_content(self):
        # check-only 且已有 manifest：内容不同也不覆盖（仅报告有变化）
        root = self._make_patch_root({"aosp/new/vendor/x/foo.h": "//x"})
        m = root / "manifest.yaml"
        m.write_text("# old content\n", encoding="utf-8")
        gm.generate_manifest(root, check_only=True,
                             kernel_deletions=[], aosp_deletions=[])
        self.assertEqual(m.read_text(encoding="utf-8"), "# old content\n")

    def test_check_only_red_when_content_changed(self):
        # 方向 2：check-only 且 manifest 有变化（未登记/缺登记等）→ 判红返
        # False（此前仅 log_info 返 True，selfcheck 接入 manifest_rc 依据此
        # 返回值——有变化即非零透出拒收据）
        root = self._make_patch_root({"aosp/new/vendor/x/foo.h": "//x"})
        m = root / "manifest.yaml"
        m.write_text("# old content\n", encoding="utf-8")
        ok = gm.generate_manifest(root, check_only=True,
                                  kernel_deletions=[], aosp_deletions=[])
        self.assertFalse(ok)
        self.assertEqual(m.read_text(encoding="utf-8"), "# old content\n")

    def test_no_change_reports_ok(self):
        # manifest 与生成内容一致：check-only 亦报无变化（不写盘）
        root = self._make_patch_root({"aosp/new/vendor/x/foo.h": "//x"})
        gm.generate_manifest(root, check_only=False,
                             kernel_deletions=[], aosp_deletions=[])
        mtime_before = (root / "manifest.yaml").stat().st_mtime_ns
        import time
        time.sleep(0.01)
        gm.generate_manifest(root, check_only=True,
                             kernel_deletions=[], aosp_deletions=[])
        self.assertEqual((root / "manifest.yaml").stat().st_mtime_ns, mtime_before)

    def test_deletions_section(self):
        # deletions 段：kernel/aosp 独立 source 前缀
        root = self._make_patch_root({})
        gm.generate_manifest(root, check_only=False,
                             kernel_deletions=["drivers/z.c"],
                             aosp_deletions=["vendor/x/z.h"])
        content = (root / "manifest.yaml").read_text(encoding="utf-8")
        self.assertIn("deletions:", content)
        self.assertIn("source: rpi5-kernel-build/common/drivers/z.c", content)
        self.assertIn("source: aosp/vendor/x/z.h", content)

    # ── 方向 5：manifest 未登记判红门禁 ─────────────────────────────
    def test_check_only_red_when_unregistered(self):
        # 文件存在但 manifest 未登记（如 Makefile 已引用而漏登的
        # lciod_read_logic.c）→ check_only 判红返 False，且不写盘
        root = self._make_patch_root({"kernel/new/x/lciod_read_logic.c": "//c"})
        m = root / "manifest.yaml"
        m.write_text("# old\n", encoding="utf-8")
        ok = gm.generate_manifest(root, check_only=True,
                                  kernel_deletions=[], aosp_deletions=[])
        self.assertFalse(ok)
        self.assertEqual(m.read_text(encoding="utf-8"), "# old\n")

    def test_regenerate_registers_unregistered(self):
        # 非 check_only 重生成：未登记文件被登记进 manifest，返回 True
        root = self._make_patch_root({"kernel/new/x/lciod_read_logic.c": "//c"})
        m = root / "manifest.yaml"
        m.write_text("# old\n", encoding="utf-8")
        ok = gm.generate_manifest(root, check_only=False,
                                  kernel_deletions=[], aosp_deletions=[])
        self.assertTrue(ok)
        self.assertIn("patch: kernel/new/x/lciod_read_logic.c",
                      m.read_text(encoding="utf-8"))

    def test_regenerate_unregistered_warns_not_errors(self):
        # CDP-12：重生成路径发现未登记文件降为 log_warn——log_error 仅留给
        # check_only 拒绝路径，消除"报 error 却登记成功"的日志与结果矛盾
        root = self._make_patch_root({"kernel/new/x/lciod_read_logic.c": "//c"})
        m = root / "manifest.yaml"
        m.write_text("# old\n", encoding="utf-8")
        with mock.patch.object(gm, "log_error") as me, \
                mock.patch.object(gm, "log_warn") as mw:
            ok = gm.generate_manifest(root, check_only=False,
                                      kernel_deletions=[], aosp_deletions=[])
        self.assertTrue(ok)
        warned = " ".join(str(c.args[0]) for c in mw.call_args_list
                          if c.args)
        self.assertIn("lciod_read_logic.c", warned)
        me.assert_not_called()

    def test_check_only_unregistered_errors_not_warns(self):
        # CDP-12：check_only 拒绝路径保留 log_error（判红门禁语义不变）
        root = self._make_patch_root({"kernel/new/x/lciod_read_logic.c": "//c"})
        m = root / "manifest.yaml"
        m.write_text("# old\n", encoding="utf-8")
        with mock.patch.object(gm, "log_error") as me, \
                mock.patch.object(gm, "log_warn") as mw:
            ok = gm.generate_manifest(root, check_only=True,
                                      kernel_deletions=[], aosp_deletions=[])
        self.assertFalse(ok)
        self.assertTrue(me.called)
        mw.assert_not_called()

    def test_fully_registered_no_red(self):
        # 全覆盖（无未登记）→ check_only 不判红返 True
        root = self._make_patch_root({"aosp/new/vendor/x/foo.h": "//x"})
        gm.generate_manifest(root, check_only=False,
                             kernel_deletions=[], aosp_deletions=[])
        self.assertTrue(gm.generate_manifest(
            root, check_only=True, kernel_deletions=[], aosp_deletions=[]))

    def test_entries_sorted_by_as_posix(self):
        # 方向 5：段内排序按 as_posix（/ 统一路径）而非 OS 原生 Path 比较
        # （Windows 分隔符 \ 参与 Path 比较会使排序依赖平台，manifest 漂移）
        root = self._make_patch_root({
            "aosp/new/vendor/z/foo.h": "//z",
            "aosp/new/vendor/a/bar.h": "//a",
            "aosp/new/vendor/m/mid.h": "//m",
        })
        gm.generate_manifest(root, check_only=False,
                             kernel_deletions=[], aosp_deletions=[])
        content = (root / "manifest.yaml").read_text(encoding="utf-8")
        # 按 as_posix 字典序：a < m < z（若按 Path 原生比较在 Windows 上
        # 可能 a < z < m 或平台相关，manifest 生成不可复现）
        self.assertLess(content.index("vendor/a/bar.h"),
                        content.index("vendor/m/mid.h"))
        self.assertLess(content.index("vendor/m/mid.h"),
                        content.index("vendor/z/foo.h"))


class TestGenManifestMark(unittest.TestCase):
    """方向 4：gen_manifest main 收尾自发 mark gen_manifest（edit 段细分）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_root = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self.batch = "abc123def456"

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old_root
        self._tmp.cleanup()

    def test_mark_gen_manifest_writes(self):
        # start 建 current-batch.json → _mark_gen_manifest 自发 mark（照
        # selfcheck._mark_selfcheck 子进程调法，batch 识别走回落）
        import cdp_timing
        from cdp_paths import log_apply_dir
        cdp_timing.main(["start", "--batch", self.batch])
        gm._mark_gen_manifest()
        data = json.loads((log_apply_dir() / f"timings-{self.batch}.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][-1]["name"], "gen_manifest")

    def test_mark_failure_not_block(self):
        # 发点子进程异常（OSError）仅 stderr warn，不改 main 返回码
        err = io.StringIO()
        with mock.patch.object(subprocess, "run",
                               side_effect=OSError("boom")), \
                contextlib.redirect_stderr(err):
            gm._mark_gen_manifest()
        self.assertIn("warn", err.getvalue())

    def _run_main(self, argv):
        """mock harness 依赖 + profile_path，以给定 argv 跑 main，返回
        _mark_gen_manifest mock（断言调用与否）。"""
        old_argv = sys.argv
        sys.argv = ["gen_manifest"] + argv
        try:
            with mock.patch.object(gm, "harness_init"), \
                    mock.patch.object(gm, "generate_manifest"), \
                    mock.patch.object(gm, "harness_exit"), \
                    mock.patch.object(gm, "profile_path",
                                      return_value=Path(tempfile.mkdtemp())), \
                    mock.patch.object(gm, "_mark_gen_manifest") as mk:
                gm.main()
        finally:
            sys.argv = old_argv
        return mk

    def test_main_check_only_no_mark(self):
        # 方向 4：check_only（selfcheck manifest_rc 内调）不发点——发点会与
        # apply_selfcheck mark 交错劫持致段 0.0
        mk = self._run_main(["--check-only"])
        mk.assert_not_called()

    def test_main_regenerate_invokes_mark(self):
        # 实际重生成（写盘）才自发打点 gen_manifest（edit 段归因）
        mk = self._run_main([])
        mk.assert_called_once()


class TestGenManifestGitFiles(unittest.TestCase):
    """方向 2：git 仓走 git ls-files（热路径防全树 rglob 39s 回归），含未
    跟踪文件（--others，覆盖工作树新 patch 未提交场景）。"""

    def _make_patch_root(self, files):
        d = Path(tempfile.mkdtemp())
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return d

    def _make_git_root(self, files, tracked=True):
        d = self._make_patch_root(files)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True,
                       capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=d,
                       check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=d,
                       check=True, capture_output=True)
        if tracked:
            subprocess.run(["git", "add", "-A"], cwd=d, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-qm", "b"], cwd=d, check=True,
                           capture_output=True)
        return d

    def test_git_ls_files_covers_untracked(self):
        # 未跟踪新 patch（未 add）仍被列出（--others --exclude-standard），
        # 不破坏"未登记即判红"门禁
        root = self._make_git_root({"aosp/new/vendor/x/foo.h": "//x",
                                    "aosp/new/vendor/x/bar.h": "//bar"},
                                   tracked=True)
        (root / "aosp" / "new" / "vendor" / "x" / "baz.h").write_text("//baz")
        rels = {f.as_posix() for f in gm._git_ls_files(root)}
        self.assertIn("aosp/new/vendor/x/foo.h", rels)
        self.assertIn("aosp/new/vendor/x/baz.h", rels)

    def test_generate_uses_git_ls_files_in_git_root(self):
        # 生成走 git ls-files：manifest 条目齐全（等价原 rglob 语义）
        root = self._make_git_root({"aosp/new/vendor/x/foo.h": "//x"},
                                   tracked=True)
        ok = gm.generate_manifest(root, check_only=False,
                                  kernel_deletions=[], aosp_deletions=[])
        self.assertTrue(ok)
        content = (root / "manifest.yaml").read_text(encoding="utf-8")
        self.assertIn("patch: aosp/new/vendor/x/foo.h", content)

    def test_non_git_falls_back_rglob(self):
        # 非 git 仓回落 rglob（豁免分支），行为与旧实现一致
        root = self._make_patch_root({"aosp/new/vendor/x/foo.h": "//x"})
        self.assertIsNone(gm._git_ls_files(root))
        files = list(gm._iter_dir_files(root / "aosp" / "new" / "vendor" / "x",
                                        root))
        self.assertEqual([f.as_posix() for f in files], ["foo.h"])


if __name__ == "__main__":
    unittest.main()

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

    def test_main_invokes_mark(self):
        # main 收尾调用 _mark_gen_manifest（mock 生成与 harness 收尾依赖，
        # 控制 sys.argv 避免读 pytest 参数）
        old_argv = sys.argv
        sys.argv = ["gen_manifest", "--check-only"]
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
        mk.assert_called_once()


if __name__ == "__main__":
    unittest.main()

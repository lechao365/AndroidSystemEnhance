import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

"""check_ioctl_headers 单测：内核/AOSP ioctl 头一致性 compare 判定。

覆盖：一致通过 / 单侧漂移判红 / 文件缺失 / 双空判红（方向 2：两侧均
未提取到 struct/enum 不得当作"一致"放行，防静默假绿）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_ioctl_headers import compare  # noqa: E402


class TestCompare(unittest.TestCase):
    def _write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".h", delete=False,
                                        encoding="utf-8")
        f.write(text)
        f.close()
        return Path(f.name)

    def test_identical_returns_ok(self):
        text = "struct foo {\n    int a;\n};\nenum bar {\n    B1,\n    B2\n};\n"
        k = self._write(text)
        a = self._write(text)
        rc, msg = compare(k, a)
        k.unlink(); a.unlink()
        self.assertEqual(rc, 0)
        self.assertIn("一致", msg)

    def test_drift_returns_red(self):
        k = self._write("struct foo {\n    int a;\n};\n")
        a = self._write("struct foo {\n    long a;\n};\n")
        rc, msg = compare(k, a)
        k.unlink(); a.unlink()
        self.assertEqual(rc, 1)
        self.assertIn("签名漂移", msg)

    def test_missing_file_returns_2(self):
        k = self._write("struct foo {\n    int a;\n};\n")
        rc, msg = compare(k, Path("/nonexistent/x.h"))
        k.unlink()
        self.assertEqual(rc, 2)
        self.assertIn("文件缺失", msg)

    def test_both_empty_returns_red(self):
        # 双空判红（方向 2）：两侧均无 struct/enum（纯注释/宏定义）→
        # rc=1 判红，此前 "(无结构/枚举)" 静默返 0 放行
        k = self._write("/* only comment */\n")
        a = self._write("#define X 1\n")
        rc, msg = compare(k, a)
        k.unlink(); a.unlink()
        self.assertEqual(rc, 1)
        self.assertIn("双空", msg)

    def test_nested_brace_block_returns_red(self):
        # lib-09 红灯：嵌套 struct（块内嵌套花括号）超出 BLOCK_RE [^}]*
        # 提取器支持范围，提取不到会静默漏检——fail-closed 判红交人工复核
        text = ("struct outer {\n"
                "    struct inner {\n"
                "        int a;\n"
                "    };\n"
                "    int b;\n"
                "};\n")
        k = self._write(text)
        a = self._write(text)
        try:
            rc, msg = compare(k, a)
        finally:
            k.unlink(); a.unlink()
        self.assertEqual(rc, 1)
        self.assertIn("嵌套", msg)
        self.assertIn("outer", msg)


if __name__ == "__main__":
    unittest.main()

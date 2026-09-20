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
        k.unlink()
        a.unlink()
        self.assertEqual(rc, 0)
        self.assertIn("一致", msg)

    def test_drift_returns_red(self):
        k = self._write("struct foo {\n    int a;\n};\n")
        a = self._write("struct foo {\n    long a;\n};\n")
        rc, msg = compare(k, a)
        k.unlink()
        a.unlink()
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
        k.unlink()
        a.unlink()
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
            k.unlink()
            a.unlink()
        self.assertEqual(rc, 1)
        self.assertIn("嵌套", msg)
        self.assertIn("outer", msg)

    def test_cmd_identical_returns_ok(self):
        # 方向 2：ioctl 命令号 guard 一致 → mode=cmd 通过（LcView 双侧形态）
        text = ("#define M  'V'\n"
                "#define GET_A  _IOR(M, 1, uint32_t)\n"
                "#define GET_S  _IOR(M, 3, struct foo)\n")
        k = self._write(text)
        a = self._write(text)
        try:
            rc, msg = compare(k, a, "cmd")
        finally:
            k.unlink()
            a.unlink()
        self.assertEqual(rc, 0)
        self.assertIn("一致", msg)

    def test_cmd_drift_returns_red(self):
        # 方向 2：命令号漂移（序号/类型不同）判红——struct 签名一致也拦不住
        # ioctl 错配，须单独比对命令号 guard
        k = self._write("#define M 'V'\n#define GET_S _IOR(M, 3, struct foo)\n")
        a = self._write("#define M 'V'\n#define GET_S _IOR(M, 4, struct foo)\n")
        try:
            rc, msg = compare(k, a, "cmd")
        finally:
            k.unlink()
            a.unlink()
        self.assertEqual(rc, 1)
        self.assertIn("命令号漂移", msg)

    def test_cmd_missing_on_one_side_red(self):
        # 方向 2：命令号仅单侧存在判红（单侧增删命令即漂移）
        k = self._write("#define M 'V'\n#define GET_A _IOR(M, 1, uint32_t)\n")
        a = self._write("#define M 'V'\n")
        try:
            rc, msg = compare(k, a, "cmd")
        finally:
            k.unlink()
            a.unlink()
        self.assertEqual(rc, 1)
        self.assertIn("仅", msg)

    def test_cmd_both_empty_returns_red(self):
        # 方向 2：mode=cmd 但两侧均无命令号 → 双空判红（不得静默放行）
        k = self._write("/* only comment */\n")
        a = self._write("#define X 1\n")
        try:
            rc, msg = compare(k, a, "cmd")
        finally:
            k.unlink()
            a.unlink()
        self.assertEqual(rc, 1)
        self.assertIn("命令号双空", msg)


if __name__ == "__main__":
    unittest.main()

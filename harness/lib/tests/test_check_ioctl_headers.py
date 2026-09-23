"""check_ioctl_headers 单测：内核/AOSP ioctl 头一致性 compare 判定。

覆盖：一致通过 / 单侧漂移判红 / 文件缺失 / 双空判红（方向 2：两侧均
未提取到 struct/enum 不得当作"一致"放行，防静默假绿）。
R-04 方向 2：跨侧契约常量值比对 + struct offsetof 自动推导交叉验证。
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_ioctl_headers import (compare, compare_constants,  # noqa: E402
                                 compare_offsetofs, derive_offsetofs)


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


# ============================================================
# R-04 方向 2：跨侧契约常量 + struct offsetof 自动推导
# ============================================================

class TestDeriveOffsetofs(unittest.TestCase):
    def test_derive_offsets_simple(self):
        # 全 uint32_t 顺序布局：偏移 0/4/8/12
        offsets, desc = derive_offsetofs([
            "uint32_t total_records;",
            "uint32_t overrun_cnt;",
            "uint32_t dropped_cnt;",
            "uint32_t ring_usage_bytes;",
            "uint32_t ring_size_bytes;",
        ])
        self.assertIsNotNone(offsets)
        self.assertEqual(offsets["total_records"], 0)
        self.assertEqual(offsets["overrun_cnt"], 4)
        self.assertEqual(offsets["dropped_cnt"], 8)
        self.assertEqual(offsets["ring_usage_bytes"], 12)
        self.assertEqual(offsets["ring_size_bytes"], 16)
        self.assertIn("5 字段", desc)

    def test_derive_offsets_mixed_types(self):
        # uint8 + uint16 + uint32 顺序累加
        offsets, _ = derive_offsetofs([
            "uint8_t flag;",
            "uint16_t count;",
            "uint32_t total;",
        ])
        self.assertEqual(offsets, {"flag": 0, "count": 1, "total": 3})

    def test_derive_offsets_unknown_type_fail_closed(self):
        # 未知类型（含指针/嵌套）→ 返回 None（无法推导即不参与比对）
        offsets, desc = derive_offsetofs(["void *ptr;"])
        self.assertIsNone(offsets)
        self.assertIn("无法", desc)


class TestCompareConstants(unittest.TestCase):
    def _repo(self, kernel_text, aosp_text):
        """搭临时 code 仓根：内核 lcview_internal.h + AOSP lcview_events.h。"""
        d = Path(tempfile.mkdtemp(prefix="ioctl_const_"))
        (d / "rpi5/kernel/new/vendor/lechao/LcView").mkdir(
            parents=True, exist_ok=True)
        (d / "rpi5/aosp/new/vendor/lechao/services/lechao_lcview/include").mkdir(
            parents=True, exist_ok=True)
        (d / "rpi5/kernel/new/vendor/lechao/LcView/lcview_internal.h").write_text(
            kernel_text, encoding="utf-8")
        (d / "rpi5/aosp/new/vendor/lechao/services/lechao_lcview/include/"
             "lcview_events.h").write_text(aosp_text, encoding="utf-8")
        return d

    def test_constants_equal_returns_ok(self):
        k = "#define LCVIEW_BUILDER_MAX_SIZE  4096\n"
        a = "#define LCVIEW_BUILDER_MAX_SIZE 4096\n"
        d = self._repo(k, a)
        try:
            rc, msg = compare_constants(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 0)
        self.assertIn("一致", msg)
        self.assertIn("1 个", msg)

    def test_constants_drift_returns_red(self):
        # 内核 4096 vs AOSP 8192 → 判红（缓冲预算与内核单条上限漂移）
        k = "#define LCVIEW_BUILDER_MAX_SIZE  4096\n"
        a = "#define LCVIEW_BUILDER_MAX_SIZE 8192\n"
        d = self._repo(k, a)
        try:
            rc, msg = compare_constants(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 1)
        self.assertIn("跨侧常量漂移", msg)
        self.assertIn("4096", msg)
        self.assertIn("8192", msg)

    def test_constants_missing_on_aosp_returns_red(self):
        # AOSP 缺镜像宏 → 判红（单侧定义即契约破坏）
        k = "#define LCVIEW_BUILDER_MAX_SIZE  4096\n"
        a = "#define OTHER_MACRO 1\n"
        d = self._repo(k, a)
        try:
            rc, msg = compare_constants(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 1)
        self.assertIn("缺镜像宏", msg)

    def test_constants_hex_equivalent_ok(self):
        # 0x1000 与 4096 数值相等 → 一致（数值口径非字面量口径）
        k = "#define LCVIEW_BUILDER_MAX_SIZE  0x1000\n"
        a = "#define LCVIEW_BUILDER_MAX_SIZE 4096\n"
        d = self._repo(k, a)
        try:
            rc, msg = compare_constants(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 0)
        self.assertIn("一致", msg)


class TestCompareOffsetofs(unittest.TestCase):
    _KERNEL = (
        "struct lcview_stats {\n"
        "    uint32_t total_records;\n"
        "    uint32_t overrun_cnt;\n"
        "    uint32_t dropped_cnt;\n"
        "    uint32_t ring_usage_bytes;\n"
        "    uint32_t ring_size_bytes;\n"
        "};\n"
    )
    _AOSP_OK = (
        "struct lcview_stats {\n"
        "    uint32_t total_records;\n"
        "    uint32_t overrun_cnt;\n"
        "    uint32_t dropped_cnt;\n"
        "    uint32_t ring_usage_bytes;\n"
        "    uint32_t ring_size_bytes;\n"
        "};\n"
        "static_assert(offsetof(struct lcview_stats, total_records) == 0, \"\");\n"
        "static_assert(offsetof(struct lcview_stats, overrun_cnt) == 4, \"\");\n"
        "static_assert(offsetof(struct lcview_stats, dropped_cnt) == 8, \"\");\n"
        "static_assert(offsetof(struct lcview_stats, ring_usage_bytes) == 12, \"\");\n"
        "static_assert(offsetof(struct lcview_stats, ring_size_bytes) == 16, \"\");\n"
    )

    def _repo(self, kernel=_KERNEL, aosp=_AOSP_OK):
        d = Path(tempfile.mkdtemp(prefix="ioctl_off_"))
        (d / "rpi5/kernel/new/vendor/lechao/LcView").mkdir(
            parents=True, exist_ok=True)
        (d / "rpi5/aosp/new/vendor/lechao/services/lechao_lcview/include").mkdir(
            parents=True, exist_ok=True)
        (d / "rpi5/kernel/new/vendor/lechao/LcView/lcview_internal.h").write_text(
            kernel, encoding="utf-8")
        (d / "rpi5/aosp/new/vendor/lechao/services/lechao_lcview/include/"
             "lcview_ioctl.h").write_text(aosp, encoding="utf-8")
        return d

    def test_offsetofs_ok(self):
        d = self._repo()
        try:
            rc, msg = compare_offsetofs(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 0)
        self.assertIn("5 个", msg)
        self.assertIn("一致", msg)

    def test_offsetof_drift_returns_red(self):
        # AOSP 断言 overrun_cnt==8 而内核推导 4 → 判红（offset 漂移）
        aosp = self._AOSP_OK.replace(
            "overrun_cnt) == 4", "overrun_cnt) == 8")
        d = self._repo(aosp=aosp)
        try:
            rc, msg = compare_offsetofs(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 1)
        self.assertIn("offsetof 漂移", msg)
        self.assertIn("overrun_cnt", msg)

    def test_offsetof_missing_assert_returns_red(self):
        # AOSP 少一个字段断言 → 判红（内核字段无对应断言）
        aosp = self._AOSP_OK.replace(
            "static_assert(offsetof(struct lcview_stats, ring_size_bytes) == 16, \"\");\n",
            "")
        d = self._repo(aosp=aosp)
        try:
            rc, msg = compare_offsetofs(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 1)
        self.assertIn("无对应 static_assert", msg)

    def test_offsetof_extra_assert_returns_red(self):
        # AOSP 多断言内核不存在的字段 → 判红（断言内容超内核布局）
        aosp = self._AOSP_OK + (
            "static_assert(offsetof(struct lcview_stats, extra_field) == 20, \"\");\n")
        d = self._repo(aosp=aosp)
        try:
            rc, msg = compare_offsetofs(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(rc, 1)
        self.assertIn("多出内核", msg)


if __name__ == "__main__":
    unittest.main()
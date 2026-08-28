import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_parse as cp

VALID_SV = """-sv base:1a2b3c4d5e6f
意图: 修复 lcview 空指针
验收: svc:lechao_lcview
方向: 检查 service.cpp 入口
"""

VALID_S = """-s base:1a2b3c4d5e6f
意图: 更新 README 映射表说明
验收: 无
方向: 补充新增文件条目描述
"""


class TestParse(unittest.TestCase):
    def test_parse_sv(self):
        b = cp.parse_batch(VALID_SV)
        self.assertEqual(b.mode, "sv")
        self.assertEqual(b.base, "1a2b3c4d5e6f")
        self.assertIn("lcview", b.intent)
        self.assertIn("svc:", b.acceptance)

    def test_parse_s(self):
        b = cp.parse_batch(VALID_S)
        self.assertEqual(b.mode, "s")
        self.assertEqual(b.acceptance, "无")

    def test_batch_id_deterministic(self):
        self.assertEqual(cp.batch_id_from_text(VALID_SV), cp.batch_id_from_text(VALID_SV))
        self.assertNotEqual(cp.batch_id_from_text(VALID_SV), cp.batch_id_from_text(VALID_S))

    def test_batch_id_immune_to_extra_spaces(self):
        # 正文行内多一空格（传输/转写引入）不得改变 batch_id（normalize 折叠连续空白）
        spaced = VALID_S.replace("更新 README 映射表说明", "更新  README  映射表说明")
        self.assertNotEqual(spaced, VALID_S)
        self.assertEqual(cp.batch_id_from_text(spaced), cp.batch_id_from_text(VALID_S))
        # 插入单空格（不形成连续空白，折叠无效）：batch_id 同样不变（删净空白再哈希）
        single = VALID_S.replace("更新 README 映射表说明", "更新 README 映射表 说明")
        self.assertNotEqual(single, VALID_S)
        self.assertEqual(cp.batch_id_from_text(single), cp.batch_id_from_text(VALID_S))

    def test_validate_ok(self):
        code, errs = cp.validate_batch(VALID_SV, role="emit")
        self.assertEqual(code, 0, errs)
        code, errs = cp.validate_batch(VALID_S, role="emit")
        self.assertEqual(code, 0, errs)

    def test_empty_batch(self):
        code, _ = cp.validate_batch("", role="emit")
        self.assertEqual(code, 12)

    def test_struct_first_line(self):
        # 首行结构错误（缺模式标记 / base 缺失）→ 11
        for bad in ["sv base:1a2b3c4d5e6f", "-sv", "-s 1a2b3c4d5e6f"]:
            code, _ = cp.validate_batch(
                bad + "\n意图: x\n验收: 无\n方向: y\n", role="emit")
            self.assertEqual(code, 11, bad)

    def test_unknown_line_exit_11(self):
        # 首行后出现不匹配 TAG_RE 的行 → 11（严格化，不再静默丢弃）
        text = "-s base:1a2b3c4d5e6f\n意图: x\n垃圾行\n验收: 无\n方向: y\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 11)

    def test_missing_tags(self):
        text = "-sv base:1a2b3c4d5e6f\n意图: 只有意图\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 14)

    def test_bad_base(self):
        # 首行结构合法但 base 非 12hex → 15（MODE_RE 放宽后才可达）
        text = "-sv base:xyz\n意图: 修复 lcview 空指针问题\n验收: svc:lechao_lcview\n方向: 检查入口\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 15)

    def test_over_budget(self):
        text = "-s base:1a2b3c4d5e6f\n意图: " + "x" * 600 + "\n验收: 无\n方向: y\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 16)
        # 16 在 apply 角色同样 blocking（仅 17 降级）
        code, _ = cp.validate_batch(text, role="apply")
        self.assertEqual(code, 16)

    def test_under_budget(self):
        text = "-s base:1a2b3c4d5e6f\n意图: a\n验收: 无\n方向: b\n"
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 16)

    def test_sv_acceptance_rule(self):
        text = VALID_SV.replace("svc:lechao_lcview", "无")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 17)

    def test_s_acceptance_must_be_wu(self):
        text = VALID_S.replace("验收: 无", "验收: svc:xx")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 17)

    def test_apply_role_softens_only_17(self):
        # validate_batch 恒返回原始码 17（降级由 main 统一处理）
        text = VALID_SV.replace("svc:lechao_lcview", "无")
        code, _ = cp.validate_batch(text, role="apply")
        self.assertEqual(code, 17)

    def test_cli_apply_softened_warn_prefix(self):
        # apply 角色 17 降级：main 返回 0，且输出 warn: 前缀（不得 error:）
        import io
        import tempfile
        from contextlib import redirect_stdout
        text = VALID_SV.replace("svc:lechao_lcview", "无")
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(text)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cp.main(["--role", "apply", path])
        self.assertEqual(rc, 0)
        self.assertIn("warn:", buf.getvalue())
        self.assertNotIn("error:", buf.getvalue())

    def test_base_match(self):
        self.assertTrue(cp.base_matches(VALID_SV, "1a2b3c4d5e6f"))
        self.assertTrue(cp.base_matches(VALID_SV, "1A2B3C4D5E6F"))
        self.assertFalse(cp.base_matches(VALID_SV, "ffffffffffff"))

    def test_cli_missing_file_exit_3(self):
        # 批次文件不可读 → 3（契约表参数错误）
        self.assertEqual(cp.main(["--role", "emit", "/nonexistent.cdp"]), 3)

    def test_cli_non_utf8_file_exit_3(self):
        # 非 UTF-8 批次文件 → 3（不得裸抛 traceback）
        import tempfile
        f = tempfile.NamedTemporaryFile("wb", suffix=".cdp", delete=False)
        f.write(b"\xff\xfe-s base:1a2b3c4d5e6f\n")
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        self.assertEqual(cp.main(["--role", "emit", path]), 3)

    def test_cli_expect_base_mismatch_exit_18(self):
        # base 不匹配本地 HEAD → 拒批 exit 18（独立码，参数/文件错误仍 3）
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        self.assertEqual(cp.main(["--role", "apply", "--expect-base",
                                  "ffffffffffff", path]), 18)
        self.assertEqual(cp.main(["--role", "apply", "--expect-base",
                                  "1a2b3c4d5e6f", path]), 0)


if __name__ == "__main__":
    unittest.main()
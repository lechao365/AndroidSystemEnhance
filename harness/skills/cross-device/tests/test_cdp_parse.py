import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_parse as cp

VALID_SV = """-sv base:1a2b3c4d5e6f
意图: 修复 lcview 空指针
验收: case:lcview-liveness
方向: 检查 service.cpp 入口
"""

VALID_S = """-s base:1a2b3c4d5e6f
意图: 更新 README 映射表说明
验收: 无
方向: 补充新增文件条目描述
"""


class TestParse(unittest.TestCase):
    def _cli(self, *argv):
        """main() 直调 + 角色环境注入（角色机器化）：按 --role 注入同名
        HARNESS_ROLE 环境变量（--gen-checksum 等无 --role 的 emit 侧命令
        缺省注入 emit），不依赖本机 paths.conf 实际配置；调用后还原。"""
        role = argv[argv.index("--role") + 1] if "--role" in argv else "emit"
        old = os.environ.get("HARNESS_ROLE")
        os.environ["HARNESS_ROLE"] = role

        def _restore():
            if old is None:
                os.environ.pop("HARNESS_ROLE", None)
            else:
                os.environ["HARNESS_ROLE"] = old
        self.addCleanup(_restore)
        return cp.main(list(argv))

    def test_parse_sv(self):
        b = cp.parse_batch(VALID_SV)
        self.assertEqual(b.mode, "sv")
        self.assertEqual(b.base, "1a2b3c4d5e6f")
        self.assertIn("lcview", b.intent)
        self.assertIn("case:", b.acceptance)

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
        code, errs = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 11)
        # CDP-11：错误消息注明"规范化后行号"（与原始批次文件行号可能不符）
        self.assertTrue(any("规范化后行号" in e for e in errs), errs)

    def test_unknown_line_lineno_normalized_not_raw(self):
        # CDP-11：原始文件含空行时原始行号与规范化行号漂移——消息行号须
        # 注明规范化口径（垃圾行原始在第 6 行；规范化剥 3 个空行后位于
        # 第 3 行：行1 首行、行2 意图、行3 垃圾行）
        text = ("-s base:1a2b3c4d5e6f\n\n\n意图: x\n\n垃圾行\n"
                "验收: 无\n方向: y\n")
        code, errs = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 11)
        self.assertTrue(any("规范化后行号 3" in e for e in errs), errs)
        self.assertFalse(any("行号 6" in e for e in errs), errs)

    def test_duplicate_tag_rejected(self):
        # 三标签各占一段且不得重复：重复标签 → 11（emit/apply 均 blocking，结构错误）
        text = "-sv base:1a2b3c4d5e6f\n意图: x\n验收: svc:a\n验收: svc:b\n方向: y\n"
        code, errs = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 11)
        self.assertTrue(any("重复标签" in e for e in errs), errs)
        # CDP-11：重复标签消息行号注明"规范化后行号"
        self.assertTrue(any("规范化后行" in e for e in errs), errs)
        code, _ = cp.validate_batch(text, role="apply")
        self.assertEqual(code, 11, "重复标签不入 SOFT_ERRORS，apply 角色同样 blocking")

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
        text = VALID_SV.replace("case:lcview-liveness", "无")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 17)

    def test_s_case_id_invalid_rejected(self):
        # 方向 2：case id 限小写字母数字与连字符，违规返 17
        for bad in ("case:LCVIEW", "case:lc view", "case:",
                    "case:lcview_liveness", "case:-lcview", "case:lcview-"):
            text = VALID_SV.replace("case:lcview-liveness", bad)
            code, _ = cp.validate_batch(text, role="emit")
            self.assertEqual(code, 17, bad)

    def test_sv_case_id_valid_ok(self):
        # 方向 2：case:<id>,<id>... 合法语法返 0（多个逗号分隔）
        text = VALID_SV.replace("case:lcview-liveness",
                                "case:lcview-liveness,lcview-pipeline")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 0)

    def test_sv_manual_free_text_valid(self):
        # 方向 2：manual 模式保留自由文本（唯一自由文本通道）返 0
        text = VALID_SV.replace("case:lcview-liveness",
                                "manual:lcview 服务运行正常")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 0)

    def test_sv_bare_text_rejected(self):
        # 方向 2：自由文本仅留 manual 模式，裸表达式（svc: 等）返 17
        text = VALID_SV.replace("case:lcview-liveness", "svc:lechao_lcview")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 17)

    def test_s_acceptance_must_be_wu(self):
        text = VALID_S.replace("验收: 无", "验收: svc:xx")
        code, _ = cp.validate_batch(text, role="emit")
        self.assertEqual(code, 17)

    # ── 批次六 C1：引号防呆（EXIT_QUOTE=19，仅 emit 角色校验）─────────

    def test_emit_quote_single_is_red(self):
        # 正文含单引号：emit 角色拒批（exit 19），apply 侧传输层会展开吞字
        quoted = VALID_SV.replace("修复 lcview 空指针", "修复 'lcview' 空指针")
        code, errs = cp.validate_batch(quoted, role="emit")
        self.assertEqual(code, cp.EXIT_QUOTE, errs)
        code, _ = cp.validate_batch(quoted, role="apply")
        self.assertEqual(code, cp.EXIT_OK)

    def test_emit_quote_double_is_red(self):
        quoted = VALID_S.replace("README 映射表说明", 'README "映射表" 说明')
        code, errs = cp.validate_batch(quoted, role="emit")
        self.assertEqual(code, cp.EXIT_QUOTE, errs)

    def test_emit_quote_free_in_any_tag_is_red(self):
        # 三段正文任一段含引号均拒（manual 自由文本同样受限）
        quoted = VALID_SV.replace("检查 service.cpp 入口",
                                  "检查 service.cpp 入口（含\"timeout\"）")
        code, errs = cp.validate_batch(quoted, role="emit")
        self.assertEqual(code, cp.EXIT_QUOTE, errs)

    def test_apply_role_ignores_quote(self):
        # apply 角色不校验引号（文本已产生，拒批只断链；残留由 heredoc
        # 写入法兜底）
        quoted = VALID_SV.replace("空指针", '"空"指针')
        code, _ = cp.validate_batch(quoted, role="apply")
        self.assertEqual(code, cp.EXIT_OK)

    def test_apply_role_softens_only_17(self):
        # validate_batch 恒返回原始码 17（降级由 main 统一处理）
        text = VALID_SV.replace("case:lcview-liveness", "无")
        code, _ = cp.validate_batch(text, role="apply")
        self.assertEqual(code, 17)

    def test_cli_apply_softened_warn_prefix(self):
        # apply 角色 17 降级：main 返回 0，且输出 warn: 前缀（不得 error:）
        import io
        import tempfile
        from contextlib import redirect_stdout
        text = VALID_SV.replace("case:lcview-liveness", "无")
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(text)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self._cli("--role", "apply", "--expect-base", "1a2b3c4d5e6f", path)
        self.assertEqual(rc, 0)
        self.assertIn("warn:", buf.getvalue())
        self.assertNotIn("error:", buf.getvalue())

    def test_base_match(self):
        self.assertTrue(cp.base_matches(VALID_SV, "1a2b3c4d5e6f"))
        self.assertTrue(cp.base_matches(VALID_SV, "1A2B3C4D5E6F"))
        self.assertFalse(cp.base_matches(VALID_SV, "ffffffffffff"))

    def test_cli_missing_file_exit_3(self):
        # 批次文件不可读 → 3（契约表参数错误）
        self.assertEqual(self._cli("--role", "emit", "/nonexistent.cdp"), 3)

    def test_cli_non_utf8_file_exit_3(self):
        # 非 UTF-8 批次文件 → 3（不得裸抛 traceback）
        import tempfile
        f = tempfile.NamedTemporaryFile("wb", suffix=".cdp", delete=False)
        f.write(b"\xff\xfe-s base:1a2b3c4d5e6f\n")
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        self.assertEqual(self._cli("--role", "emit", path), 3)

    def test_cli_expect_base_mismatch_exit_18(self):
        # base 不匹配本地 HEAD → 拒批 exit 18（独立码，参数/文件错误仍 3）
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        self.assertEqual(self._cli("--role", "apply", "--expect-base",
                                 "ffffffffffff", path), 18)
        self.assertEqual(self._cli("--role", "apply", "--expect-base",
                                 "1a2b3c4d5e6f", path), 0)

    def test_cli_apply_missing_expect_base_exit_18(self):
        # 方向 4：apply 角色未传 --expect-base → 18（不再静默跳过 base 校验）
        import io
        import tempfile
        from contextlib import redirect_stdout
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self._cli("--role", "apply", path)
        self.assertEqual(rc, 18)
        self.assertIn("--expect-base", buf.getvalue())

    def test_cli_emit_without_expect_base_ok(self):
        # 方向 4：emit 角色未传 --expect-base 不强制（仅 apply 强制）
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        self.assertEqual(self._cli("--role", "emit", path), 0)

    def test_cli_apply_pass_emits_precheck_mark(self):
        # A-1：apply 角色通过后解析器自发 mark precheck（脚本自发替代 AI
        # 手打，B-1 实测已证伪手动依赖）；emit 角色不打点
        import io
        import os
        import tempfile
        from contextlib import redirect_stdout
        from unittest import mock
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_root = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = tmp.name
        old_batch = os.environ.pop("CDP_BATCH_ID", None)

        def _restore():
            if old_root is None:
                os.environ.pop("CDP_PROJECT_ROOT", None)
            else:
                os.environ["CDP_PROJECT_ROOT"] = old_root
            if old_batch is not None:
                os.environ["CDP_BATCH_ID"] = old_batch
        self.addCleanup(_restore)

        # 构造活跃批打点文件（start），apply 通过后 mark 应追加 precheck
        import cdp_timing
        cdp_timing.main(["start", "--batch", "1a2b3c4d5e6f".replace("", "")[:0] + "abc123def456"])
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self._cli("--role", "apply", "--expect-base",
                        "1a2b3c4d5e6f", path)
        self.assertEqual(rc, 0)
        marks = cdp_timing.read_marks("abc123def456")
        self.assertEqual([m["name"] for m in marks], ["precheck"])

    def test_cli_emit_pass_no_precheck_mark(self):
        # emit 角色通过后不打点（emit 侧无活跃 apply 批）
        import io
        import os
        import tempfile
        from contextlib import redirect_stdout
        from unittest import mock
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_root = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = tmp.name
        old_batch = os.environ.pop("CDP_BATCH_ID", None)

        def _restore():
            if old_root is None:
                os.environ.pop("CDP_PROJECT_ROOT", None)
            else:
                os.environ["CDP_PROJECT_ROOT"] = old_root
            if old_batch is not None:
                os.environ["CDP_BATCH_ID"] = old_batch
        self.addCleanup(_restore)

        import cdp_timing
        cdp_timing.main(["start", "--batch", "abc123def456"])
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self._cli("--role", "emit", path)
        self.assertEqual(rc, 0)
        marks = cdp_timing.read_marks("abc123def456")
        self.assertEqual(marks, [])

    # ── 角色机器化：apply 入口跨角色拦截 ──────────────────────────────

    def test_cli_role_mismatch_blocked(self):
        # --role apply 但本机角色为 emit → ROLE_MISMATCH exit 1（参数解析
        # 后、副作用发生前拦截；测试经环境变量注入角色，不依赖本机
        # paths.conf 实际配置）
        import io
        import tempfile
        from contextlib import redirect_stderr, redirect_stdout
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        old = os.environ.get("HARNESS_ROLE")
        os.environ["HARNESS_ROLE"] = "emit"

        def _restore():
            if old is None:
                os.environ.pop("HARNESS_ROLE", None)
            else:
                os.environ["HARNESS_ROLE"] = old
        self.addCleanup(_restore)
        err_buf = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err_buf):
            with self.assertRaises(SystemExit) as cm:
                cp.main(["--role", "apply", "--expect-base",
                         "1a2b3c4d5e6f", path])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("ROLE_MISMATCH", err_buf.getvalue())
        self.assertIn("emit", err_buf.getvalue())

    # ── CDP 批次 checksum（emit 生成 / apply 校验）────────────────────

    def test_with_checksum_inserts_line_after_header(self):
        # emit 生成：首行后插入 checksum 行，值 = 首行（mode+base）+ 正文
        # （checksum 行以下全部行）规范化后 sha256 前 16 位；正文行保持原样
        out = cp.with_checksum(VALID_SV)
        lines = out.splitlines()
        self.assertEqual(lines[0], "-sv base:1a2b3c4d5e6f")
        m = cp.CHECKSUM_RE.match(lines[1])
        self.assertIsNotNone(m, lines[1])
        import hashlib
        covered = lines[0] + "\n" + "\n".join(lines[2:])
        expect = hashlib.sha256(
            cp.normalize_batch_text(covered).encode("utf-8")).hexdigest()[:16]
        self.assertEqual(m.group(1), expect)
        self.assertEqual("\n".join(lines[2:]),
                         "\n".join(VALID_SV.splitlines()[1:]))

    def test_with_checksum_leading_blank_lines_valid_both_roles(self):
        # CDP-01：原始批次首行前有空行时 with_checksum 先规范化再定位首行
        # （与解析口径对称）——旧行为按原文 splitlines 定位会把 checksum 行
        # 插在空行之前，emit selfcheck（normalize 删空行）绿灯而 apply 恒拒
        raw = "\n\n" + VALID_SV
        out = cp.with_checksum(raw)
        lines = out.splitlines()
        self.assertEqual(lines[0], "-sv base:1a2b3c4d5e6f")
        self.assertRegex(lines[1], r"^checksum: [0-9a-f]{16}$")
        code, errs = cp.validate_batch(out, role="emit")
        self.assertEqual(code, 0, errs)
        code, _ = cp.validate_batch(out, role="apply")
        self.assertEqual(code, 0)

    def test_with_checksum_idempotent_and_refresh(self):
        # 原位刷新：已有 checksum 行的批次再跑 with_checksum 输出不变；
        # 正文变更后 checksum 随之变化
        once = cp.with_checksum(VALID_SV)
        self.assertEqual(cp.with_checksum(once), once)
        edited = once.replace("修复 lcview 空指针", "修复 lcview 越界访问")
        refreshed = cp.with_checksum(edited)
        self.assertNotEqual(refreshed, once)
        self.assertNotEqual(refreshed, edited)

    def test_validate_with_checksum_ok_both_roles(self):
        # 生成后自检：checksum 正确时 emit/apply 双角色均通过
        code, errs = cp.validate_batch(cp.with_checksum(VALID_SV), role="emit")
        self.assertEqual(code, 0, errs)
        code, _ = cp.validate_batch(cp.with_checksum(VALID_SV), role="apply")
        self.assertEqual(code, 0)

    def test_checksum_mismatch_rejected_both_roles(self):
        # 篡改正文一行 → CHECKSUM_MISMATCH（exit 1，双角色 blocking）
        batch = cp.with_checksum(VALID_SV).replace("检查 service.cpp 入口",
                                                   "检查 service.cpp 出口")
        for role in ("emit", "apply"):
            code, errs = cp.validate_batch(batch, role=role)
            self.assertEqual(code, cp.EXIT_CHECKSUM, (role, errs))
            self.assertTrue(any("CHECKSUM_MISMATCH" in e for e in errs), errs)

    def test_checksum_first_line_tamper_rejected_both_roles(self):
        # CDP-02：首行（mode+base）纳入 checksum 覆盖——-sv→-s / base 篡改
        # 后 checksum 校验失败判红（exit 1，双角色 blocking），不再经
        # exit 17（ACCEPTANCE 软错）降级 WARN 放行致验证等级静默降级
        batch = cp.with_checksum(VALID_SV)
        mode_tampered = batch.replace("-sv base:1a2b3c4d5e6f",
                                      "-s base:1a2b3c4d5e6f", 1)
        self.assertNotEqual(mode_tampered, batch)
        for role in ("emit", "apply"):
            code, errs = cp.validate_batch(mode_tampered, role=role)
            self.assertEqual(code, cp.EXIT_CHECKSUM, (role, errs))
            self.assertTrue(any("CHECKSUM_MISMATCH" in e for e in errs), errs)
        base_tampered = batch.replace("base:1a2b3c4d5e6f",
                                      "base:ffffffffffff", 1)
        code, errs = cp.validate_batch(base_tampered, role="apply")
        self.assertEqual(code, cp.EXIT_CHECKSUM, errs)

    def test_checksum_line_wrong_position_struct_error(self):
        # checksum 行不在紧跟首行的头部位置 → 按未知行报 11（结构错误）
        bad = ("-s base:1a2b3c4d5e6f\n意图: x\nchecksum: " + "a" * 16
               + "\n验收: 无\n方向: y\n")
        code, _ = cp.validate_batch(bad, role="emit")
        self.assertEqual(code, 11)

    def test_cli_checksum_mismatch_exit_1(self):
        # apply 侧 CLI：篡改正文 → 非零退出（EXIT_CHECKSUM=1）且输出含
        # CHECKSUM_MISMATCH 分类字样
        import io
        import tempfile
        from contextlib import redirect_stdout
        batch = cp.with_checksum(VALID_SV).replace("修复 lcview 空指针",
                                                   "修复 lcview 空引用")
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(batch)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self._cli("--role", "apply", "--expect-base",
                           "1a2b3c4d5e6f", path)
        self.assertEqual(rc, cp.EXIT_CHECKSUM)
        self.assertIn("CHECKSUM_MISMATCH", buf.getvalue())

    def test_cli_old_batch_without_checksum_warn_compat(self):
        # 旧格式无 checksum 行 → warn 兼容放行（exit 0）
        import io
        import tempfile
        from contextlib import redirect_stdout
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self._cli("--role", "apply", "--expect-base",
                           "1a2b3c4d5e6f", path)
        self.assertEqual(rc, 0)
        self.assertIn("无 checksum", buf.getvalue())

    def test_cli_gen_checksum_outputs_valid_batch(self):
        # emit 产批收尾 CLI：--gen-checksum 输出整批（含 checksum 行），
        # 与 with_checksum 同值；输出回灌自检（--role emit）须 exit 0
        import io
        import tempfile
        from contextlib import redirect_stdout
        f = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                        encoding="utf-8")
        f.write(VALID_SV)
        f.close()
        path = f.name
        self.addCleanup(Path(path).unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self._cli("--gen-checksum", path)
        self.assertEqual(rc, 0)
        generated = buf.getvalue()
        self.assertEqual(generated, cp.with_checksum(VALID_SV))
        self.assertIn("checksum: ", generated)
        f2 = tempfile.NamedTemporaryFile("w", suffix=".cdp", delete=False,
                                         encoding="utf-8")
        f2.write(generated)
        f2.close()
        self.addCleanup(Path(f2.name).unlink)
        self.assertEqual(self._cli("--role", "emit", f2.name), 0)


if __name__ == "__main__":
    unittest.main()
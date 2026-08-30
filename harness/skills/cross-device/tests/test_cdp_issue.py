import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_issue
import cdp_paths

_FIELD_RE = cdp_issue._FIELD_RE
_TASK = "lcview-refactor"


def _mk_issue(issue_id="KI-20260829-001", status="open", origin="introduced",
              title="lcview 重复落盘计数异常"):
    return cdp_issue.Issue(
        schema_version=1,
        issue_id=issue_id,
        title=title,
        discovered_in="38433d446f07",
        origin=origin,
        blocking=True,
        blocking_reason="影响数据一致性",
        status=status,
        task=_TASK,
        resolved_in="",
        batch_id="18f27638d9f6",
    )


def _header_fields(text):
    header = text.split("\n## body", 1)[0]
    return [m.group(1) for m in _FIELD_RE.finditer(header)]


def _raw_header(text):
    header = text.split("\n## body", 1)[0]
    return {m.group(1): m.group(2).strip() for m in _FIELD_RE.finditer(header)}


class TestIssue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._dir = cdp_paths.data_known_issues_dir()

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    # ── 方向 1：task 为稳定标识，非修复方向描述 ─────────────────────────
    def test_header_fields_equal_template_and_fields(self):
        # 写出头字段集 == 模板头字段集 == _FIELDS（方向 5 强制三源一致）
        self.assertEqual(cdp_issue.template_header_fields(), cdp_issue._FIELDS)
        r = _mk_issue()
        p = cdp_issue.write_issue(r, "现场: x")
        self.assertEqual(_header_fields(p.read_text(encoding="utf-8")),
                         cdp_issue._FIELDS)

    def test_write_read_roundtrip(self):
        r = _mk_issue()
        p = cdp_issue.write_issue(r, "复现步骤: 重复写入")
        # 命名: 时间戳-batch_id-slug
        self.assertTrue(p.name.endswith("-18f27638d9f6-"
                                        "lcview-重复落盘计数异常.md"), p.name)
        got = cdp_issue.read_issue(p)
        self.assertEqual(got.issue_id, "KI-20260829-001")
        self.assertEqual(got.title, "lcview 重复落盘计数异常")
        self.assertEqual(got.discovered_in, "38433d446f07")
        self.assertEqual(got.origin, "introduced")
        self.assertTrue(got.blocking)
        self.assertEqual(got.blocking_reason, "影响数据一致性")
        self.assertEqual(got.status, "open")
        # task 为大颗粒任务稳定标识（门禁按此过滤，修法描述入正文）
        self.assertEqual(got.task, _TASK)
        self.assertEqual(got.resolved_in, "")

    def test_body_fields_do_not_bleed(self):
        # 正文含 "- status: wontfix" 等 key-value 行不得覆盖头部字段
        r = _mk_issue(status="open", origin="introduced")
        p = cdp_issue.write_issue(
            r, "## 现场\n- status: wontfix\n- issue_id: fake000\n- blocking: false\n- origin: pre-existing")
        got = cdp_issue.read_issue(p)
        self.assertEqual(got.status, "open")
        self.assertEqual(got.issue_id, "KI-20260829-001")
        self.assertTrue(got.blocking)
        self.assertEqual(got.origin, "introduced")

    def test_keeps_all_within_quota(self):
        # 未超配额：写入多份全部保留（对照收据 _DETAIL_KEEP=20 的 prune）
        for i in range(5):
            cdp_issue.write_issue(_mk_issue(f"KI-20260829-00{i}"), f"body{i}")
        self.assertEqual(len(cdp_issue.issue_files(self._dir)), 5)

    def test_prune_keeps_20_issues(self):
        # 超配额：写 25 份只保留最新 20，index 与文件集一致（validate 无红）
        for i in range(25):
            cdp_issue.write_issue(_mk_issue(f"KI-20260829-{i:03d}"), f"body{i}")
        files = cdp_issue.issue_files(self._dir)
        self.assertEqual(len(files), cdp_issue._ISSUE_KEEP)
        self.assertEqual(len(files), 20)
        self.assertEqual(cdp_issue.validate_issue(files[-1], self._dir), [])

    def test_origin_status_invalid_falls_back(self):
        # 非法枚举回落默认值，不崩
        r = cdp_issue.Issue.from_text(
            "- origin: badvalue\n- status: badvalue\n## body\n\nx\n")
        self.assertEqual(r.origin, "introduced")
        self.assertEqual(r.status, "open")

    def test_from_text_strips_values(self):
        # 头字段值带尾随空格应正常解析（与 validate_issue 的 strip 语义一致）
        r = cdp_issue.Issue.from_text(
            "- schema_version: 1 \n- issue_id: KI-X \n- title: t \n"
            "- discovered_in: abc \n- origin: introduced \n- blocking: true \n"
            "- blocking_reason: r \n- status: open \n- task: t \n"
            "- resolved_in:  \n\n## body\n\nx\n")
        self.assertEqual(r.origin, "introduced")
        self.assertEqual(r.status, "open")
        self.assertTrue(r.blocking)
        self.assertEqual(r.schema_version, 1)

    def test_slug_derivation_from_title(self):
        self.assertEqual(cdp_issue._slug_from_title("lcview 重复落盘!"),
                         "lcview-重复落盘")

    # ── 方向 2：issue_files 显式排除 index.md ──────────────────────────
    def test_issue_files_excludes_index(self):
        cdp_issue.write_issue(_mk_issue(), "x")
        self.assertTrue((self._dir / "index.md").exists())
        self.assertEqual([f.name for f in cdp_issue.issue_files(self._dir)],
                         [f for f in sorted(p.name for p in
                                            self._dir.glob("*.md"))
                          if f != "index.md"])

    # ── 方向 4：index.md 一行一条，write_issue/状态变更均回写 ──────────
    def test_write_issue_builds_index(self):
        cdp_issue.write_issue(_mk_issue("KI-20260829-001", title="问题甲"), "x")
        cdp_issue.write_issue(_mk_issue("KI-20260829-002", status="fixed",
                                        origin="pre-existing", title="问题乙"), "y")
        entries = cdp_issue.read_index(self._dir)
        self.assertEqual(len(entries), 2)
        by_id = {e["issue_id"]: e for e in entries}
        e1 = by_id["KI-20260829-001"]
        self.assertEqual(e1["origin"], "introduced")
        self.assertTrue(e1["blocking"])
        self.assertEqual(e1["task"], _TASK)
        self.assertEqual(e1["status"], "open")
        self.assertEqual(by_id["KI-20260829-002"]["status"], "fixed")

    def test_set_status_updates_file_and_index(self):
        p = cdp_issue.write_issue(_mk_issue(), "x")
        cdp_issue.set_status(p, "fixed", self._dir)
        got = cdp_issue.read_issue(p)
        self.assertEqual(got.status, "fixed")
        entries = cdp_issue.read_index(self._dir)
        self.assertEqual(entries[0]["status"], "fixed")

    def test_set_status_rejects_invalid(self):
        p = cdp_issue.write_issue(_mk_issue(), "x")
        with self.assertRaises(ValueError):
            cdp_issue.set_status(p, "badvalue", self._dir)

    # ── 方向 3：validate_issue 判红 ─────────────────────────────────────
    def test_validate_issue_ok(self):
        p = cdp_issue.write_issue(_mk_issue(), "现场")
        self.assertEqual(cdp_issue.validate_issue(p, self._dir), [])

    def test_validate_missing_field(self):
        r = _mk_issue()
        text = (r.header_lines() + "\n## body\n\nx\n").replace(
            "- resolved_in: ", "- removed: ", 1)
        p = self._dir / "20260829-180000-18f27638d9f6-bad.md"
        p.write_text(text, encoding="utf-8")
        errs = cdp_issue.validate_issue(p, self._dir)
        self.assertTrue(any("resolved_in" in e for e in errs), errs)

    def test_validate_bad_enum(self):
        p = self._dir / "20260829-180000-18f27638d9f6-bad.md"
        p.write_text(
            "- schema_version: 1\n- issue_id: KI-X\n- title: t\n"
            "- discovered_in: 38433d446f07\n- origin: badvalue\n"
            "- blocking: false\n- blocking_reason: \n- status: badstatus\n"
            "- task: lcview-refactor\n- resolved_in: \n\n## body\n\nx\n",
            encoding="utf-8")
        errs = cdp_issue.validate_issue(p, self._dir)
        self.assertTrue(any("origin 非法" in e for e in errs), errs)
        self.assertTrue(any("status 非法" in e for e in errs), errs)

    def test_validate_blocking_without_reason(self):
        p = self._dir / "20260829-180000-18f27638d9f6-bad.md"
        p.write_text(
            "- schema_version: 1\n- issue_id: KI-X\n- title: t\n"
            "- discovered_in: 38433d446f07\n- origin: introduced\n"
            "- blocking: true\n- blocking_reason: \n- status: open\n"
            "- task: lcview-refactor\n- resolved_in: \n\n## body\n\nx\n",
            encoding="utf-8")
        errs = cdp_issue.validate_issue(p, self._dir)
        self.assertTrue(any("blocking_reason" in e for e in errs), errs)

    def test_validate_bad_filename(self):
        p = self._dir / "notes.md"
        p.write_text(_mk_issue().header_lines() + "\n## body\n\nx\n",
                     encoding="utf-8")
        errs = cdp_issue.validate_issue(p, self._dir)
        self.assertTrue(any("文件名式样" in e for e in errs), errs)

    def test_validate_index_file_set_consistency(self):
        p1 = cdp_issue.write_issue(_mk_issue("KI-20260829-001"), "x")
        cdp_issue.write_issue(_mk_issue("KI-20260829-002"), "y")
        # 删掉一个文件 → index 有多余条目
        p1.unlink()
        errs = cdp_issue.validate_issue(p1, self._dir)
        self.assertTrue(any("多余条目" in e for e in errs), errs)
        # 手工制造不在 index 中的文件 → index 缺文件条目
        stray = self._dir / "20260829-190000-18f27638d9f6-stray.md"
        stray.write_text(_mk_issue("KI-STRAY").header_lines() + "\n## body\n\nx\n",
                         encoding="utf-8")
        errs2 = cdp_issue.validate_issue(stray, self._dir)
        self.assertTrue(any("缺该问题条目" in e for e in errs2), errs2)
        self.assertTrue(any("缺文件条目" in e for e in errs2), errs2)


if __name__ == "__main__":
    unittest.main()

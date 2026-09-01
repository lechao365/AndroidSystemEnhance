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
              severity="P2", title="lcview 重复落盘计数异常"):
    return cdp_issue.Issue(
        schema_version=1,
        issue_id=issue_id,
        title=title,
        discovered_in="38433d446f07",
        origin=origin,
        severity=severity,
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
        self.assertEqual(got.severity, "P2")
        self.assertTrue(got.blocking)
        self.assertEqual(got.blocking_reason, "影响数据一致性")
        self.assertEqual(got.status, "open")
        # task 为大颗粒任务稳定标识（门禁按此过滤，修法描述入正文）
        self.assertEqual(got.task, _TASK)
        self.assertEqual(got.resolved_in, "")

    def test_write_issue_rejects_invalid_batch_id(self):
        # batch_id 非 12 位小写 hex → 写时抛错（畸形文件名不留到 promote 才暴露）
        for bad in ("", "abc", "18F27638D9F6", "18f27638d9f6-", "18f27638d9f"):
            r = _mk_issue()
            r.batch_id = bad
            with self.assertRaises(ValueError) as cm:
                cdp_issue.write_issue(r, "现场")
            self.assertIn("batch_id 非法", str(cm.exception))

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
        # 写时不再老化（KIR-006 promote 清算）：写入多份全部保留，无配额
        for i in range(5):
            cdp_issue.write_issue(_mk_issue(f"KI-20260829-00{i}"), f"body{i}")
        self.assertEqual(len(cdp_issue.issue_files(self._dir)), 5)

    def test_write_no_aging_keeps_all_terminal(self):
        # 终态条目写时全留（无配额老化）：25 条 fixed 与 wontfix 写后一份不删
        for i in range(25):
            r = _mk_issue(f"KI-20260829-{i:03d}", status="fixed")
            r.blocking = False
            r.blocking_reason = ""
            cdp_issue.write_issue(r, f"body{i}")
        files = cdp_issue.issue_files(self._dir)
        self.assertEqual(len(files), 25)
        self.assertEqual(cdp_issue.validate_issue(files[-1], self._dir), [])

    # ── 方向 1：promote 清算（closed_issue_ids + delete_closed）───────────
    def test_closed_issue_ids_only_terminal(self):
        # 终态只看 status：fixed 与 wontfix 计入清单，open/scheduled 不算，
        # fixed 即使 blocking=true 也计入（不看 blocking）
        cdp_issue.write_issue(_mk_issue("KI-FIXED", status="fixed"), "x")
        cdp_issue.write_issue(_mk_issue("KI-WONTFIX", status="wontfix"), "y")
        cdp_issue.write_issue(_mk_issue("KI-OPEN"), "z")
        cdp_issue.write_issue(_mk_issue("KI-SCHED", status="scheduled"), "w")
        closed = set(cdp_issue.closed_issue_ids(self._dir))
        self.assertEqual(closed, {"KI-FIXED", "KI-WONTFIX"})

    def test_closed_issue_details_include_identity(self):
        # 明细清单每项含 issue_id/resolved_in/title（删文件后仍可辨认），
        # open/scheduled 不入清单
        r1 = _mk_issue("KI-FIXED", status="fixed")
        r1.resolved_in = "abc123def456"
        cdp_issue.write_issue(r1, "x")
        cdp_issue.write_issue(_mk_issue("KI-WONTFIX", status="wontfix"), "y")
        cdp_issue.write_issue(_mk_issue("KI-OPEN"), "z")
        details = cdp_issue.closed_issue_details(self._dir)
        self.assertEqual(
            sorted(details, key=lambda d: d["issue_id"]), [
                {"issue_id": "KI-FIXED", "resolved_in": "abc123def456",
                 "title": "lcview 重复落盘计数异常"},
                {"issue_id": "KI-WONTFIX", "resolved_in": "",
                 "title": "lcview 重复落盘计数异常"},
            ])

    def test_closed_fixed_blocking_still_terminal(self):
        # blocking=true 的 fixed 条目同样属终态（终态判定不看 blocking）
        r = _mk_issue("KI-BLK-FIXED", status="fixed")
        r.blocking = True  # _mk_issue 默认 blocking=True
        cdp_issue.write_issue(r, "x")
        self.assertEqual(cdp_issue.closed_issue_ids(self._dir), ["KI-BLK-FIXED"])

    def test_delete_closed_removes_files_and_syncs_index(self):
        # 清算删除：终态文件删除 + index 同步重建，活项（open/scheduled）全留
        cdp_issue.write_issue(_mk_issue("KI-FIXED", status="fixed"), "x")
        cdp_issue.write_issue(_mk_issue("KI-WONTFIX", status="wontfix"), "y")
        cdp_issue.write_issue(_mk_issue("KI-OPEN"), "z")
        cdp_issue.write_issue(_mk_issue("KI-SCHED", status="scheduled"), "w")
        removed = cdp_issue.delete_closed(["KI-FIXED", "KI-WONTFIX"], self._dir)
        self.assertEqual(len(removed), 2)
        remaining = {i.issue_id for p in cdp_issue.issue_files(self._dir)
                     for i in [cdp_issue.read_issue(p)]}
        self.assertEqual(remaining, {"KI-OPEN", "KI-SCHED"})
        # index 与文件集一致（删除后重建，无悬空条目）
        index_ids = {e["issue_id"] for e in cdp_issue.read_index(self._dir)}
        self.assertEqual(index_ids, remaining)
        self.assertNotIn("KI-FIXED", index_ids)
        self.assertNotIn("KI-WONTFIX", index_ids)

    def test_delete_closed_unknown_ids_ignored(self):
        # 清单含不存在的 id：静默跳过，不影响其余删除
        cdp_issue.write_issue(_mk_issue("KI-FIXED", status="fixed"), "x")
        cdp_issue.write_issue(_mk_issue("KI-OPEN"), "z")
        removed = cdp_issue.delete_closed(["KI-FIXED", "KI-GHOST"], self._dir)
        self.assertEqual(len(removed), 1)
        self.assertEqual({i.issue_id for p in cdp_issue.issue_files(self._dir)
                          for i in [cdp_issue.read_issue(p)]}, {"KI-OPEN"})

    def test_origin_status_invalid_falls_back(self):
        # 非法枚举回落默认值，不崩
        r = cdp_issue.Issue.from_text(
            "- origin: badvalue\n- severity: badvalue\n- status: badvalue\n"
            "## body\n\nx\n")
        self.assertEqual(r.origin, "introduced")
        self.assertEqual(r.severity, "P2")
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
            "- severity: badvalue\n- blocking: false\n- blocking_reason: \n"
            "- status: badstatus\n- task: lcview-refactor\n- resolved_in: \n"
            "\n## body\n\nx\n",
            encoding="utf-8")
        errs = cdp_issue.validate_issue(p, self._dir)
        self.assertTrue(any("origin 非法" in e for e in errs), errs)
        self.assertTrue(any("severity 非法" in e for e in errs), errs)
        self.assertTrue(any("status 非法" in e for e in errs), errs)

    def test_validate_bad_severity_red(self):
        # severity 非法枚举判红（方向 2：validate_issue 增 severity 非法判红）
        p = self._dir / "20260829-180000-18f27638d9f6-bad.md"
        p.write_text(
            "- schema_version: 1\n- issue_id: KI-X\n- title: t\n"
            "- discovered_in: 38433d446f07\n- origin: introduced\n"
            "- severity: P3\n- blocking: false\n- blocking_reason: \n"
            "- status: open\n- task: lcview-refactor\n- resolved_in: \n"
            "\n## body\n\nx\n",
            encoding="utf-8")
        errs = cdp_issue.validate_issue(p, self._dir)
        self.assertTrue(any("severity 非法" in e for e in errs), errs)
        self.assertFalse(any("头字段缺失" in e for e in errs), errs)

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

    def test_validate_task_with_whitespace_red(self):
        # task 含空白判红：read_index 按空格切分 index 行，task 内空白会列错位
        p = self._dir / "20260829-180000-18f27638d9f6-bad.md"
        p.write_text(
            "- schema_version: 1\n- issue_id: KI-X\n- title: t\n"
            "- discovered_in: 38433d446f07\n- origin: introduced\n"
            "- blocking: false\n- blocking_reason: \n- status: open\n"
            "- task: fix lcview bug\n- resolved_in: \n\n## body\n\nx\n",
            encoding="utf-8")
        errs = cdp_issue.validate_issue(p, self._dir)
        self.assertTrue(any("task 含空白" in e for e in errs), errs)

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


class TestBackfilledSeverity(unittest.TestCase):
    """回填后仓库真实 data/known-issues 全部条目 validate 无红（防堵 promote 门禁）。"""

    def test_all_repo_issues_pass_validation(self):
        # 不设 CDP_PROJECT_ROOT：data_known_issues_dir() 回落仓库真实路径
        os.environ.pop("CDP_PROJECT_ROOT", None)
        files = cdp_issue.issue_files()
        self.assertTrue(files, "data/known-issues 下应存在存量条目")
        for p in files:
            errs = cdp_issue.validate_issue(p)
            self.assertEqual(errs, [], f"{p.name}: {errs}")
            severity = cdp_issue.read_issue(p).severity
            self.assertIn(severity, cdp_issue._SEVERITIES)


if __name__ == "__main__":
    unittest.main()

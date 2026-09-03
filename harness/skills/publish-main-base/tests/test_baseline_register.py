import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baseline_register as br  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cross-device" / "lib" / "python"))
from cdp_receipt import Receipt, write_receipt  # noqa: E402


def _initial_config():
    return "# baseline 状态登记\nbaselines: []\n"


class TestBaselineRegister(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        os.environ["CDP_PROJECT_ROOT"] = str(self._root)
        self._config = self._root / "baseline-status.yaml"
        self._config.write_text(_initial_config(), encoding="utf-8")
        br.CONFIG = self._config

    def tearDown(self):
        br.CONFIG = Path(br.__file__).resolve().parents[2] / "config" / "baseline-status.yaml"
        os.environ.pop("CDP_PROJECT_ROOT", None)
        self._tmp.cleanup()

    def _make_receipt(self, build="pass", board="pass", cases="lcview-liveness"):
        r = Receipt(batch_id="batch-test", batch_base="", verified_commit="abc",
                    verify_mode="board", result="pass", build=build,
                    push_board=board, acceptance="ok", elapsed_s=10,
                    summary="test", cases=cases)
        return str(write_receipt(r, "body"))

    def _run(self, *args):
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            rc = br.main(list(args))
        return rc, buf.getvalue() + err.getvalue()

    def test_add_candidate_reads_receipt(self):
        # candidate 必须实读收据：build=build、board_verify=push_board，均大写；
        # package 无打包证据记 UNKNOWN（不再把 build_result 复制给 package_result）
        rp = self._make_receipt(build="pass", board="pass")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertIn("candidate:", out)
        b = br.load()["baselines"][0]
        self.assertEqual(b["status"], "candidate")
        self.assertEqual(b["build_result"], "PASS")
        self.assertEqual(b["package_result"], "UNKNOWN")
        self.assertNotEqual(b["package_result"], b["build_result"])
        self.assertEqual(b["board_verify"], "PASS")
        self.assertEqual(b["evidence"]["build_result"], "PASS")
        self.assertEqual(b["evidence"]["package_result"], "UNKNOWN")
        self.assertEqual(b["evidence"]["board_verify"], "PASS")
        self.assertEqual(b["evidence_scope"], "lcview-liveness")
        self.assertEqual(b["evidence"]["evidence_scope"], "lcview-liveness")
        self.assertEqual(b["sync_manifest"], rp)
        self.assertEqual(b["evidence"]["sync_manifest"], rp)

    def test_add_candidate_lowercase_receipt(self):
        # 收据 build=skip 等小写值须转大写登记，不硬编码 PASS
        rp = self._make_receipt(build="skip", board="skip")
        rc, _ = self._run("add-candidate", "--receipt-path", rp,
                          "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["build_result"], "SKIP")
        self.assertEqual(b["board_verify"], "SKIP")

    def test_add_candidate_missing_evidence_scope(self):
        # 缺 --evidence-scope 且收据无 cases：证据推导无源，拒绝登记（退 1）
        rp = self._make_receipt(cases="")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123")
        self.assertEqual(rc, 1)
        self.assertIn("--evidence-scope", out)
        self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_evidence_scope_defaults_from_cases(self):
        # 缺 --evidence-scope 且收据含 cases → 缺省推导（取收据实测范围）
        rp = self._make_receipt(cases="lcview-liveness,lcview-transfer,lcview-perf")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["evidence_scope"], "lcview-liveness,lcview-perf,lcview-transfer")
        self.assertEqual(b["evidence"]["evidence_scope"], b["evidence_scope"])

    def test_add_candidate_evidence_scope_manual_subset_ok(self):
        # 人工传值为收据 cases 子集 → 放行（收窄声明合法）
        rp = self._make_receipt(cases="lcview-liveness,lcview-transfer")
        rc, _ = self._run("add-candidate", "--receipt-path", rp,
                          "--source-commit", "abc123",
                          "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["evidence_scope"], "lcview-liveness")

    def test_add_candidate_evidence_scope_overshoot_rejected(self):
        # 人工传值超出收据 cases → 拒绝（过度声称：未实测范围不得登记）
        rp = self._make_receipt(cases="lcview-liveness")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness,lcview-perf")
        self.assertEqual(rc, 1)
        self.assertIn("过度声称", out)
        self.assertIn("lcview-perf", out)
        self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_missing_receipt_path(self):
        # 缺 --receipt-path：证据链要求实读收据，必须拒绝
        rc, out = self._run("add-candidate")
        self.assertEqual(rc, 1)
        self.assertIn("--receipt-path", out)
        self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_dedup_same_source_commit(self):
        # 同 source_commit 重复登记：复用既有 candidate，不新增记录
        rp = self._make_receipt()
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertIn("candidate:", out)
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertIn("candidate 复用", out)
        self.assertEqual(len(br.load()["baselines"]), 1)

    def test_add_candidate_dedup_updates_receipt(self):
        # 复用且收据路径不同：对齐最新证据（sync_manifest/build/board 更新）
        rp1 = self._make_receipt(build="pass", board="pass")
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp1,
                                   "--source-commit", "abc123",
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        rp2 = self._make_receipt(build="skip", board="skip")
        rc, out = self._run("add-candidate", "--receipt-path", rp2,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertIn("candidate 复用并更新收据", out)
        b = br.load()["baselines"][0]
        self.assertEqual(b["sync_manifest"], rp2)
        self.assertEqual(b["build_result"], "SKIP")
        self.assertEqual(b["board_verify"], "SKIP")
        self.assertEqual(len(br.load()["baselines"]), 1)

    def test_add_candidate_bad_receipt_path(self):
        # --receipt-path 指向不存在/非法文件：拒绝
        rc, out = self._run("add-candidate", "--receipt-path",
                            str(self._root / "no-such-receipt.md"),
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 1)
        self.assertIn("读取收据失败", out)
        self.assertEqual(br.load()["baselines"], [])

    def _write_raw_receipt(self, **kw):
        fields = dict(batch_id="batch-test", batch_base="", verified_commit="abc",
                      verify_mode="board", result="pass", build="pass",
                      push_board="pass", acceptance="ok", elapsed_s=10,
                      summary="test", cases="lcview-liveness")
        fields.update(kw)
        r = Receipt(**fields)
        return str(write_receipt(r, "body"))

    def test_add_candidate_rejects_fail(self):
        # 方向 4：build/board_verify 为 FAIL 拒绝登记（防绕过 shell 直调登记）
        for kw in ({"build": "fail"}, {"push_board": "fail"}):
            rp = self._write_raw_receipt(**kw)
            rc, out = self._run("add-candidate", "--receipt-path", rp,
                                "--source-commit", "abc123",
                                "--evidence-scope", "lcview-liveness")
            self.assertEqual(rc, 1, kw)
            self.assertIn("拒绝登记", out)
            self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_rejects_bad_enum(self):
        # 方向 4：verify_mode/result 非法枚举拒绝登记
        for kw in ({"verify_mode": "bogus"}, {"result": "bogus"}):
            rp = self._write_raw_receipt(**kw)
            rc, out = self._run("add-candidate", "--receipt-path", rp,
                                "--source-commit", "abc123",
                                "--evidence-scope", "lcview-liveness")
            self.assertEqual(rc, 1, kw)
            self.assertIn("拒绝登记", out)
            self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_rejects_missing_required(self):
        # 方向 4：缺必需字段（batch_id/verified_commit/build/push_board）拒绝登记
        rp = self._write_raw_receipt(batch_id="")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 1)
        self.assertIn("缺必需字段", out)
        self.assertEqual(br.load()["baselines"], [])

    def test_promote_requires_candidate(self):
        # 非 candidate 状态 promote 必须拒绝（门禁可信）
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        self.assertEqual(self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")[0], 0)
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")
        self.assertEqual(rc, 1)
        self.assertIn("仅 candidate 可 promote", out)

    def test_promote_creates_evidence_snapshot(self):
        # promote 落盘证据快照：data/baselines/<id>-<收据名>.md，内容与收据一致
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        self.assertTrue(snapshot.is_file(), f"快照未落盘: {snapshot}")
        self.assertEqual(snapshot.read_text(encoding="utf-8"),
                         Path(rp).read_text(encoding="utf-8"))

    def test_promote_unknown_package_warns_not_blocks(self):
        # 方向 2：promote 遇 package_result=UNKNOWN 仅告警放行，不新增阻断
        # （当前无打包生产者，硬门禁会锁死发布通道）
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["package_result"], "UNKNOWN")
        bid = b["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid,
                            "--approved-by", "lechao")
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)
        self.assertIn("package_result=UNKNOWN", out)
        self.assertIn("仅告警放行", out)

    def test_promote_missing_approved_by_rejected(self):
        # 方向 6：promote 空审批人即拒（不再回落默认常量，审批凭据外部化），
        # 拒绝后 status 保持 candidate、不产生快照
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid)
        self.assertEqual(rc, 1)
        self.assertIn("--approved-by", out)
        self.assertEqual(br.load()["baselines"][0]["status"], "candidate")
        self.assertEqual(list((self._root / "data" / "baselines").glob("*.md")),
                         [])

    def test_promote_duplicate_snapshot_rejected(self):
        # 重复 promote（revert 后再 promote）：快照已存在即拒，不得覆盖历史证据
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        self.assertEqual(self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")[0], 0)
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        snapshot.write_text("历史证据，不可覆盖", encoding="utf-8")
        # 回退 candidate 后再次 promote：应命中快照已存在而拒绝
        self.assertEqual(self._run("revert-candidate", "--baseline-id", bid)[0], 0)
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")
        self.assertEqual(rc, 1)
        self.assertIn("快照已存在", out)
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "历史证据，不可覆盖")

    def test_promote_rewrites_evidence_scope(self):
        # promote 透传 --evidence-scope：改写条目与 evidence 中的范围（如 no-code-change）
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, _ = self._run("promote", "--baseline-id", bid, "--approved-by", "lechao",
                          "--evidence-scope", "no-code-change")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["evidence_scope"], "no-code-change")
        self.assertEqual(b["evidence"]["evidence_scope"], "no-code-change")

    # ── 方向 1/2：promote 清算终态条目（KIR-006 promote 清算）────────────
    def _write_issue_files(self):
        # 在 CDP_PROJECT_ROOT 下写 known-issues：2 终态 + 1 活项（blocking 混杂）
        from cdp_issue import Issue, write_issue
        base = dict(schema_version=1, discovered_in="abc", severity="P2",
                    task="t1", batch_id="18f27638d9f6")
        write_issue(Issue(issue_id="KI-CLOSE-1", title="问题一",
                          origin="pre-existing", blocking=False,
                          status="fixed", resolved_in="abc123", **base), "x")
        write_issue(Issue(issue_id="KI-CLOSE-2", title="问题二",
                          origin="introduced", blocking=True,
                          blocking_reason="影响一致性", status="wontfix", **base), "y")
        write_issue(Issue(issue_id="KI-OPEN-1", title="问题三",
                          origin="pre-existing", blocking=False,
                          status="open", **base), "z")

    def test_promote_writes_known_issues_closed_and_deletes(self):
        # promote 清算：清单（明细列表：issue_id/resolved_in/title）先入
        # evidence.known_issues_closed 再 save，随后删终态文件（不看 blocking），
        # 活项全留，index 同步重建
        from cdp_issue import issue_files, read_index
        self._write_issue_files()
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)
        b = br.load()["baselines"][0]
        # 清单入档为明细列表（删文件后仍可辨认条目，含 resolved_in 与 title）
        self.assertEqual(b["evidence"]["known_issues_closed"], [
            {"issue_id": "KI-CLOSE-1", "resolved_in": "abc123",
             "title": "问题一"},
            {"issue_id": "KI-CLOSE-2", "resolved_in": "",
             "title": "问题二"},
        ])
        # 终态全删、活项全留（CDP_PROJECT_ROOT 指向 tmp，见 setUp）
        issues_dir = self._root / "data" / "known-issues"
        remaining = {i.issue_id for p in issue_files(issues_dir)
                     for i in [br.read_issue(p)]}
        self.assertEqual(remaining, {"KI-OPEN-1"})
        self.assertEqual({e["issue_id"] for e in read_index(issues_dir)},
                         {"KI-OPEN-1"})

    def test_promote_skips_cleanup_when_evidence_not_dict(self):
        # evidence 非字典写不成清单 → 跳过清算删除并告警（无清单入档即删 =
        # 无快照删证据），promote 照常完成且终态文件保留
        self._write_issue_files()
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        b = br.load()["baselines"][0]
        data = br.load()
        data["baselines"][0]["evidence"] = "not-a-dict"
        br.save(data)
        bid = b["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")
        self.assertEqual(rc, 0)
        self.assertIn("evidence 非字典", out)
        self.assertIn("跳过清算删除", out)
        self.assertIn("promoted:", out)
        # 终态文件未被删除（无清单入档不删），状态仍 promoted
        from cdp_issue import issue_files
        issues_dir = self._root / "data" / "known-issues"
        remaining = {i.issue_id for p in issue_files(issues_dir)
                     for i in [br.read_issue(p)]}
        self.assertEqual(remaining, {"KI-CLOSE-1", "KI-CLOSE-2", "KI-OPEN-1"})
        self.assertEqual(br.load()["baselines"][0]["status"], "promoted")

    def test_promote_delete_failure_keeps_snapshot(self):
        # 删失败不回滚快照（KIR-006）：delete_closed 抛错仅 warn，
        # promoted 状态与证据快照保留
        from unittest import mock
        self._write_issue_files()
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        with mock.patch("baseline_register.delete_closed",
                        side_effect=OSError("perm denied")):
            rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")
        self.assertEqual(rc, 0)
        self.assertIn("清算删除失败", out)
        self.assertIn("promoted:", out)
        b = br.load()["baselines"][0]
        self.assertEqual(b["status"], "promoted")
        self.assertEqual(b["evidence"]["known_issues_closed"], [
            {"issue_id": "KI-CLOSE-1", "resolved_in": "abc123",
             "title": "问题一"},
            {"issue_id": "KI-CLOSE-2", "resolved_in": "",
             "title": "问题二"},
        ])
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        self.assertTrue(snapshot.is_file(), "删失败快照仍须保留")
        # 快照内容 = 收据原文（清单入档后仍一致）
        self.assertEqual(snapshot.read_text(encoding="utf-8"),
                         Path(rp).read_text(encoding="utf-8"))

    def test_revert_candidate_requires_promoted(self):
        # 仅 promoted 可 revert-candidate：直接对 candidate revert 必须拒绝
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("revert-candidate", "--baseline-id", bid)
        self.assertEqual(rc, 1)
        self.assertIn("仅 promoted 可 revert-candidate", out)
        # promote 后再 revert 成功
        self.assertEqual(self._run("promote", "--baseline-id", bid, "--approved-by", "lechao")[0], 0)
        rc, out = self._run("revert-candidate", "--baseline-id", bid)
        self.assertEqual(rc, 0)
        self.assertIn("reverted-candidate:", out)
        b = br.load()["baselines"][0]
        self.assertEqual(b["status"], "candidate")
        self.assertNotIn("approved_by", b)
        self.assertNotIn("approved_at", b)


    def test_save_preserves_only_header_comments(self):
        # 头部 # 注释保留；yaml 条目内注释不得提前到 header（防反复上提）
        self._config.write_text(
            "# 头部注释 A\n# 头部注释 B\n\nbaselines:\n"
            "  - baseline_id: BL-1\n    status: promoted\n"
            "    # 条目内注释\n    description: x\n",
            encoding="utf-8")
        data = br.load()
        br.save(data)
        text = self._config.read_text(encoding="utf-8")
        self.assertIn("# 头部注释 A\n", text)
        self.assertIn("# 头部注释 B\n", text)
        header = text.split("baselines:", 1)[0]
        # 条目内注释不得提前到 header（safe_dump 丢弃 body 注释属 PyYAML 正常行为）
        self.assertNotIn("条目内注释", header)

    def test_save_keeps_header_comments_only_once(self):
        # 多次 save 不把条目内注释反复上提（header 稳定不增长）
        self._config.write_text(
            "# 头部\n\nbaselines:\n  - baseline_id: BL-1\n    # 条目内\n    description: x\n",
            encoding="utf-8")
        data = br.load()
        for _ in range(2):
            br.save(data)
        text = self._config.read_text(encoding="utf-8")
        self.assertEqual(text.count("# 头部"), 1)
        self.assertNotIn("条目内", text.split("baselines:", 1)[0])


if __name__ == "__main__":
    unittest.main()

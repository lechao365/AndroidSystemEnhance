import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baseline_register as br  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cross-device" / "lib" / "python"))
from cdp_receipt import Receipt, write_receipt  # noqa: E402


def _initial_config():
    return "# baseline 状态登记\nbaselines: []\n"


# 发布全量组门禁基准 = 真实 verify-cases.yaml cases 段全部 case（promote 门禁
# 与 evidence.cases_coverage 均据此核对；测试随配置同源，防漂移）
_FULL_CASES = ",".join(br.verify_case_ids())


class TestBaselineRegister(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        os.environ["CDP_PROJECT_ROOT"] = str(self._root)
        self._config = self._root / "baseline-status.yaml"
        self._config.write_text(_initial_config(), encoding="utf-8")
        br.CONFIG = self._config
        # P1-B 审批独立门禁打桩（KI-20260907-001）：operator 固定为执行人、
        # _read_approval_token 返预设值、env token 匹配——既有 promote 成功
        # 路径测试免逐个包装；审批人统一传 reviewer（≠ operator）
        self._promote_gates = [
            mock.patch("baseline_register._collect_operator",
                       return_value="lechao <lechao@x.com>"),
            mock.patch("baseline_register._read_approval_token",
                       return_value="tok-abc"),
            mock.patch.dict("os.environ",
                            {"LC_PROMOTE_APPROVAL_TOKEN": "tok-abc"}),
        ]
        for g in self._promote_gates:
            g.start()
            self.addCleanup(g.stop)

    def tearDown(self):
        br.CONFIG = Path(br.__file__).resolve().parents[2] / "config" / "baseline-status.yaml"
        os.environ.pop("CDP_PROJECT_ROOT", None)
        self._tmp.cleanup()

    def _make_receipt(self, build="pass", board="pass", cases="lcview-liveness",
                      package=None):
        r = Receipt(batch_id="batch-test", batch_base="", verified_commit="abc",
                    verify_mode="board", result="pass", build=build,
                    push_board=board, acceptance="ok", elapsed_s=10,
                    summary="test", cases=cases,
                    package=(json.dumps(package, ensure_ascii=False,
                                        separators=(",", ":"))
                             if package is not None else ""))
        return str(write_receipt(r, "body"))

    def _make_receipt_pkg(self, rc=0, **kw):
        """PASS 收据且内嵌 ws_package 打包证据（script_rc 缺省 0）——
        promote 一致性校验（方向 3）以收据 package 字段为准，须内嵌才可晋升。
        cases 缺省全量（发布全量组门禁通过所需；promote 用例直接达标）。"""
        kw.setdefault("package", {"run_id": "r", "batch_id": "batch-test",
                                  "script_rc": rc})
        kw.setdefault("cases", _FULL_CASES)
        return self._make_receipt(**kw)

    def _run(self, *args):
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            # 承重门禁数据源固定仓库真实根（方向：不随 CDP_PROJECT_ROOT 改道），
            # 测试经 --known-issues-dir 显式指回临时根，与收据等 env 隔离并存
            rc = br.main(list(args) + ["--known-issues-dir",
                                       str(self._root / "data" / "known-issues")])
        return rc, buf.getvalue() + err.getvalue()

    def _patch_no_code_changes(self, changes):
        """mock no-code-change 机器核对（方向 1）：本文件单测无真实 git 仓，
        _code_changes_since_main 恒 None，no-code-change promote 须注入核验结果。"""
        from unittest import mock
        return mock.patch("baseline_register._code_changes_since_main",
                          return_value=changes)

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

    # ── 方向 4：add-candidate 自执 known-issues 门禁（复用 check_issues_gate）
    def test_check_issues_gate_default_source_is_real_root(self):
        # 方向 3：门禁缺省数据源 = 仓库真实根，不随 CDP_PROJECT_ROOT（setUp
        # 指向临时根）改道——CI 把 CDP_PROJECT_ROOT 设到 runner 临时目录时
        # 承重门禁仍读真实登记，不被空目录 empty-registry 假绿关掉
        from baseline_register import _real_known_issues_dir
        from cdp_issue import issue_files
        real = _real_known_issues_dir()
        self.assertEqual(real,
                         Path(br.__file__).resolve().parents[3]
                         / "data" / "known-issues")
        self.assertNotEqual(real, self._root / "data" / "known-issues")
        # 真实根确有登记数据（非空），缺省门禁基于真实根判定而非临时空目录
        self.assertGreater(len(issue_files(real)), 0)
        # 显式 --known-issues-dir 注入临时根空目录 → empty-registry 放行
        # （测试注入路径：把门禁数据源指回 fixture 隔离的临时根）
        rc, out = self._run("check-issues")
        self.assertEqual(rc, 0)
        self.assertIn("empty-registry", out)

    def test_add_candidate_gate_rejects_blocking_open(self):
        # add-candidate 自执门禁：目标 task 存在未解决阻塞（introduced/blocking
        # 且未 fixed）→ 拒登记（不再只记 --ki-gate 参数不自执）
        from cdp_issue import Issue, write_issue
        base = dict(schema_version=1, discovered_in="abc", severity="P2",
                    task="t1", batch_id="18f27638d9f6")
        write_issue(Issue(issue_id="KI-OPEN-BLK", title="阻塞未解决",
                          origin="introduced", blocking=True,
                          blocking_reason="r", status="open", **base), "x")
        rp = self._make_receipt(build="pass", board="pass")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 1)
        self.assertIn("未解决阻塞", out)

    # ── 方向 5：add-candidate 拒收 device_dirty 收据 ────────────────────
    def test_add_candidate_rejects_device_dirty(self):
        # 收据 device_dirty=true（teardown 恢复失败，设备态不可信）→ 拒收登记
        from cdp_receipt import Receipt, write_receipt
        r = Receipt(batch_id="batch-dirty", batch_base="", verified_commit="abc",
                    verify_mode="board", result="pass", build="pass",
                    push_board="pass", acceptance="ok", elapsed_s=10,
                    summary="dirty", cases="lcview-liveness",
                    device_dirty="true")
        rp = str(write_receipt(r, "body"))
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 1)
        self.assertIn("device_dirty", out)

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

    def test_add_candidate_rejects_fail_result_receipt(self):
        # pub-01 红灯：result=fail 收据（build/push_board=PASS、cases 失败）不得
        # 登记——堵绕过链（fail 收据经 skip 收据成为 LATEST 后被 prepare 取作
        # evidence 锚点放行登记；AGENTS.md 登记门禁：result 属 pass 或 skip）
        rp = self._write_raw_receipt(result="fail", build="pass",
                                     push_board="pass")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 1)
        self.assertIn("result=fail", out)
        self.assertIn("登记门禁", out)
        self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_rejects_board_skip_result(self):
        # pub-01 红灯：board 收据 result=skip（上板实测 skip 不具备证据性）拒登记
        rp = self._write_raw_receipt(result="skip", verify_mode="board")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 1)
        self.assertIn("非 pass", out)
        self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_rejects_unknown_build(self):
        # pub-03 红灯：build/board_verify=UNKNOWN 视同 FAIL（AGENTS.md）拒登记
        for kw in ({"build": "unknown"}, {"push_board": "unknown"}):
            rp = self._write_raw_receipt(**kw)
            rc, out = self._run("add-candidate", "--receipt-path", rp,
                                "--source-commit", "abc123",
                                "--evidence-scope", "lcview-liveness")
            self.assertEqual(rc, 1, kw)
            self.assertIn("UNKNOWN 视同 FAIL", out)
            self.assertEqual(br.load()["baselines"], [])

    def test_add_candidate_explicit_id_duplicate_rejected(self):
        # pub-06 红灯：显式 --baseline-id 已存在（任意状态）→ 拒绝，不产生重复 id
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--source-commit", "abc123",
                                   "--evidence-scope", "lcview-liveness",
                                   "--baseline-id", "BL-DUP-01")[0], 0)
        rp2 = self._make_receipt()
        rc, out = self._run("add-candidate", "--receipt-path", rp2,
                            "--source-commit", "def456",
                            "--evidence-scope", "lcview-liveness",
                            "--baseline-id", "BL-DUP-01")
        self.assertEqual(rc, 1)
        self.assertIn("已存在", out)
        self.assertEqual(len(br.load()["baselines"]), 1)

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
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        self.assertEqual(self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")[0], 0)
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 1)
        self.assertIn("仅 candidate 可 promote", out)

    def test_promote_creates_evidence_snapshot(self):
        # promote 落盘证据快照：data/baselines/<id>-<收据名>.md，内容与收据一致
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        self.assertTrue(snapshot.is_file(), f"快照未落盘: {snapshot}")
        self.assertEqual(snapshot.read_text(encoding="utf-8"),
                         Path(rp).read_text(encoding="utf-8"))

    def test_promote_unknown_package_blocked(self):
        # 方向 3（批次 ff33f92060ac）promote 硬门禁：package_result 非 PASS 且
        # evidence_scope 非 no-code-change 即阻断（ws_package 打包生产者已就位，
        # 替换旧"UNKNOWN 仅告警"口径），拒绝后 status 保持 candidate
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["package_result"], "UNKNOWN")
        bid = b["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid,
                            "--approved-by", "reviewer")
        self.assertEqual(rc, 1)
        self.assertIn("promote 硬门禁", out)
        self.assertEqual(br.load()["baselines"][0]["status"], "candidate")

    def _make_candidate_bid(self):
        """登记一个可 promote 的 candidate（PASS 收据 + 全量 cases），返 baseline_id。"""
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        return br.load()["baselines"][0]["baseline_id"]

    def test_promote_rejects_approver_same_as_operator(self):
        # KI-20260907-001：审批人恒等执行人 → 拒（身份不等式硬校验）
        bid = self._make_candidate_bid()
        with mock.patch.object(br, "_collect_operator",
                               return_value="lechao <lechao@x.com>"), \
                mock.patch.object(br, "_read_approval_token",
                                  return_value="tok-abc"), \
                mock.patch.dict("os.environ",
                                {"LC_PROMOTE_APPROVAL_TOKEN": "tok-abc"}):
            rc = self._run("promote", "--baseline-id", bid,
                           "--approved-by", "lechao <lechao@x.com>")[0]
        self.assertNotEqual(rc, 0)

    def test_promote_rejects_token_mismatch(self):
        # env token ≠ 预设值 → 凭据外部化失败即拒（≠ promote-approval.env）
        bid = self._make_candidate_bid()
        with mock.patch.dict("os.environ",
                             {"LC_PROMOTE_APPROVAL_TOKEN": "wrong-tok"}):
            rc = self._run("promote", "--baseline-id", bid,
                           "--approved-by", "reviewer <r@x.com>")[0]
        self.assertNotEqual(rc, 0)

    def test_promote_approver_diff_and_token_match_ok(self):
        # 不同审批人 + token 匹配 → 放行（回归既有 promote 成功路径）
        bid = self._make_candidate_bid()
        with mock.patch.object(br, "_collect_operator",
                               return_value="lechao <lechao@x.com>"), \
                mock.patch.object(br, "_read_approval_token",
                                  return_value="tok-abc"), \
                mock.patch.dict("os.environ",
                                {"LC_PROMOTE_APPROVAL_TOKEN": "tok-abc"}):
            rc = self._run("promote", "--baseline-id", bid,
                           "--approved-by", "reviewer <r@x.com>")[0]
        self.assertEqual(rc, 0)

    # ── 方向 1/2（本批意图 1/2）：发布全量组覆盖核对（promote 门禁 + evidence 记录）──
    def _full_cases_without(self, drop):
        """全量 11 case 中剔除指定 case（构造缺项收据用，同 BL-20260905-01 场景）。"""
        return ",".join(c for c in br.verify_case_ids() if c != drop)

    def test_add_candidate_records_cases_coverage(self):
        # 方向 2：evidence 自描述——覆盖核对结果（result/missing/run_count）入档，
        # 复核不需要重读 verify-cases.yaml
        rp = self._make_receipt(cases=self._full_cases_without("lcview-trigger"))
        rc, _ = self._run("add-candidate", "--receipt-path", rp,
                          "--source-commit", "abc123",
                          "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        cov = br.load()["baselines"][0]["evidence"]["cases_coverage"]
        self.assertEqual(cov["result"], "partial")
        self.assertEqual(cov["missing"], ["lcview-trigger"])
        self.assertEqual(cov["run_count"], 10)
        # 全量 → full
        rp2 = self._make_receipt(cases=_FULL_CASES)
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp2,
                                   "--source-commit", "def456",
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        cov2 = br.load()["baselines"][1]["evidence"]["cases_coverage"]
        self.assertEqual(cov2["result"], "full")
        self.assertEqual(cov2["missing"], [])
        self.assertEqual(cov2["run_count"], len(br.verify_case_ids()))

    def test_promote_blocks_partial_case_coverage(self):
        # 方向 1：收据 cases 缺项（package PASS 但少跑）→ 发布全量组门禁阻断，
        # 列出缺失名（模拟 BL-20260905-01 自称全量实录缺 lcview-trigger），
        # status 保持 candidate
        rp = self._make_receipt_pkg(
            cases=self._full_cases_without("lcview-trigger"))
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 1)
        self.assertIn("发布全量组门禁", out)
        self.assertIn("lcview-trigger", out)
        self.assertIn("实跑 10", out)
        self.assertEqual(br.load()["baselines"][0]["status"], "candidate")

    def test_promote_cases_gate_exempts_no_code_change(self):
        # 无代码改动豁免（与 package 硬门禁同口径）：partial/空 cases 也可晋升
        rp = self._make_receipt(cases="lcview-liveness")
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "no-code-change")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        with self._patch_no_code_changes([]):
            rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer",
                                "--evidence-scope", "no-code-change")
        self.assertEqual(rc, 0, out)
        self.assertIn("promoted:", out)

    def test_promote_full_case_coverage_passes(self):
        # 全量覆盖正常晋升（发布全量组门禁通过路径）
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 0, out)
        self.assertIn("promoted:", out)

    # ── 方向 3：未闭环 flake 类 KI 阻断 promote ──────────────────────────
    def test_open_flake_issues_only_unclosed_flake(self):
        # _open_flake_issues：只认 kind=flake 且未标终态的条目（普通 open 不算）
        from cdp_issue import Issue, write_issue
        write_issue(Issue(issue_id="KI-FLAKE-01", title="[flake] a",
                          kind="flake", origin="pre-existing", blocking=False,
                          status="open", task="", discovered_in="abc",
                          batch_id="18f27638d9f6"), "抖动")
        write_issue(Issue(issue_id="KI-FLAKE-02", title="[flake] b",
                          kind="flake", origin="pre-existing", blocking=False,
                          status="fixed", resolved_in="abc", task="",
                          discovered_in="abc", batch_id="18f27638d9f6"), "闭环")
        write_issue(Issue(issue_id="KI-OPEN-01", title="普通问题",
                          status="open", task="t1", discovered_in="abc",
                          batch_id="18f27638d9f6"), "普通")
        from baseline_register import _open_flake_issues
        flakes = _open_flake_issues(self._root / "data" / "known-issues")
        self.assertEqual(len(flakes), 1)
        self.assertIn("[flake] a", flakes[0])

    def test_promote_blocks_open_flake(self):
        # 存在未闭环 flake（kind=flake 且 open）→ promote 拒绝（抖动未闭环
        # 不得晋升；闭环须标 fixed/wontfix 并填 resolved_in）
        from cdp_issue import Issue, write_issue
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        write_issue(Issue(issue_id="KI-FLAKE-01", title="[flake] x",
                          kind="flake", origin="pre-existing", blocking=False,
                          status="open", task="", discovered_in="abc",
                          batch_id="18f27638d9f6"), "抖动")
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 1)
        self.assertIn("未闭环 flake", out)

    def test_add_candidate_package_pass_from_evidence(self):
        # 方向 2：ws_package 证据 script_rc=0 → package_result 记 PASS
        # （按收据 batch_id 探测 log/workspace-verify/package-<batch_id>.json）
        rp = self._make_receipt(build="pass", board="pass",
                                cases="lcview-liveness")
        ev_dir = self._root / "harness" / "log" / "workspace-verify"
        ev_dir.mkdir(parents=True)
        (ev_dir / "package-batch-test.json").write_text(
            json.dumps({"run_id": "r", "batch_id": "batch-test",
                        "script_rc": 0}), encoding="utf-8")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["package_result"], "PASS")
        self.assertEqual(b["evidence"]["package_result"], "PASS")
        self.assertEqual(b["evidence"]["package_rc"], 0)
        self.assertIn("package-batch-test.json",
                      b["evidence"]["package_evidence"])

    def test_add_candidate_package_nonzero_rc_stays_unknown(self):
        # 证据 rc 非 0（打包失败如实记录）：不声称 PASS，留 UNKNOWN
        rp = self._make_receipt(build="pass", board="pass",
                                cases="lcview-liveness")
        ev_dir = self._root / "harness" / "log" / "workspace-verify"
        ev_dir.mkdir(parents=True)
        (ev_dir / "package-batch-test.json").write_text(
            json.dumps({"run_id": "r", "script_rc": 1,
                        "error": "镜像缺失: vendor.img"}), encoding="utf-8")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertEqual(br.load()["baselines"][0]["package_result"], "UNKNOWN")

    def test_add_candidate_sudo_n_false_warns_manual_path(self):
        # 方向 5：UNKNOWN 且证据 sudo_n=false（opencode 会话 NoNewPrivileges
        # 使 sudo 恒被内核拒绝）→ 告警指向人工打包路径（BLD-013），登记不阻断
        rp = self._make_receipt(build="pass", board="pass",
                                cases="lcview-liveness")
        ev_dir = self._root / "harness" / "log" / "workspace-verify"
        ev_dir.mkdir(parents=True)
        (ev_dir / "package-batch-test.json").write_text(
            json.dumps({"run_id": "r", "script_rc": None, "sudo_n": False,
                        "error": "sudo 非交互探测失败"}), encoding="utf-8")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertEqual(br.load()["baselines"][0]["package_result"], "UNKNOWN")
        self.assertIn("sudo_n=false", out)
        self.assertIn("BLD-013", out)
        self.assertIn("会话外普通终端", out)

    def test_add_candidate_sudo_n_false_no_warn_on_pass(self):
        # sudo_n=false 但证据 script_rc=0（PASS）→ 不告警（打包已成功，无需人工路径）
        rp = self._make_receipt_pkg(rc=0)
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertEqual(br.load()["baselines"][0]["package_result"], "PASS")
        self.assertNotIn("sudo_n=false", out)

    def test_add_candidate_no_code_change_skips_package(self):
        # 方向 2：evidence_scope=no-code-change（打包豁免）→ 记 SKIP，
        # 无打包证据也不记 UNKNOWN
        rp = self._make_receipt(build="pass", board="pass", cases="")
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--source-commit", "abc123",
                                   "--evidence-scope", "no-code-change")[0], 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["package_result"], "SKIP")
        self.assertEqual(b["evidence"]["package_result"], "SKIP")

    def test_add_candidate_package_evidence_explicit_override(self):
        # --package-evidence 显式路径优先于 batch_id 探测
        rp = self._make_receipt(build="pass", board="pass",
                                cases="lcview-liveness")
        ev = self._root / "custom-package.json"
        ev.write_text(json.dumps({"script_rc": 0}), encoding="utf-8")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness",
                            "--package-evidence", str(ev))
        self.assertEqual(rc, 0)
        self.assertEqual(br.load()["baselines"][0]["package_result"], "PASS")

    def test_add_candidate_package_from_receipt_field(self):
        # 方向 2（本批意图 2）：收据 package 字段内嵌打包证据（随收据入库
        # 可追溯）→ script_rc=0 记 PASS，package_evidence 指向收据载体
        rp = self._make_receipt_pkg(rc=0)
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["package_result"], "PASS")
        self.assertEqual(b["evidence"]["package_result"], "PASS")
        self.assertEqual(b["evidence"]["package_rc"], 0)
        self.assertEqual(b["evidence"]["package_evidence"], rp)

    def test_add_candidate_receipt_package_priority_over_file(self):
        # 收据内嵌证据优先于 --package-evidence/探测文件（内嵌=入库可追溯主源）；
        # 文件证据 rc=1 不得覆盖收据证据 rc=0 的 PASS
        rp = self._make_receipt_pkg(rc=0)
        ev = self._root / "conflicting-package.json"
        ev.write_text(json.dumps({"run_id": "r", "script_rc": 1}),
                      encoding="utf-8")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness",
                            "--package-evidence", str(ev))
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["package_result"], "PASS")
        self.assertEqual(b["evidence"]["package_rc"], 0)

    def test_promote_consistency_mismatch_blocked(self):
        # 方向 3（本批意图 3）：基线记 PASS（gitignore 文件证据）但收据无内嵌
        # 打包证据（不可追溯）→ promote 一致性校验推导 UNKNOWN ≠ PASS 即阻断，
        # 拒绝后 status 保持 candidate 且不落快照
        rp = self._make_receipt(build="pass", board="pass",
                                cases="lcview-liveness")
        ev_dir = self._root / "harness" / "log" / "workspace-verify"
        ev_dir.mkdir(parents=True)
        (ev_dir / "package-batch-test.json").write_text(
            json.dumps({"run_id": "r", "script_rc": 0}), encoding="utf-8")
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        self.assertEqual(br.load()["baselines"][0]["package_result"], "PASS")
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid,
                            "--approved-by", "reviewer")
        self.assertEqual(rc, 1)
        self.assertIn("一致性校验", out)
        self.assertIn("UNKNOWN", out)
        self.assertEqual(br.load()["baselines"][0]["status"], "candidate")
        self.assertEqual(list((self._root / "data" / "baselines").glob("*.md")),
                         [])

    def test_promote_consistency_match_passes(self):
        # 收据内嵌打包证据 rc=0 → 一致性推导 PASS 与基线一致 → 放行晋升
        rp = self._make_receipt_pkg(rc=0)
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid,
                            "--approved-by", "reviewer")
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)

    def test_promote_no_code_change_unknown_allowed_and_skips_package(self):
        # no-code-change 不受限（方向 3 豁免）：UNKNOWN 经 scope 改写为 SKIP 后放行
        rp = self._make_receipt(build="pass", board="pass", cases="")
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--source-commit", "abc123",
                                   "--evidence-scope", "no-code-change")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        with self._patch_no_code_changes([]):
            rc, out = self._run("promote", "--baseline-id", bid,
                                "--approved-by", "reviewer")
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)
        b = br.load()["baselines"][0]
        self.assertEqual(b["package_result"], "SKIP")
        self.assertEqual(b["evidence"]["package_result"], "SKIP")

    def test_promote_evidence_scope_rewrite_updates_package_skip(self):
        # promote 时 scope 改写为 no-code-change：UNKNOWN 同步改 SKIP（同源推导）
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        with self._patch_no_code_changes([]):
            rc, _ = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer",
                              "--evidence-scope", "no-code-change")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["evidence_scope"], "no-code-change")
        self.assertEqual(b["package_result"], "SKIP")

    def test_promote_missing_approved_by_rejected(self):
        # 方向 6：promote 空审批人即拒（不再回落默认常量，审批凭据外部化），
        # 拒绝后 status 保持 candidate、不产生快照
        rp = self._make_receipt_pkg()
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
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        self.assertEqual(self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")[0], 0)
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        snapshot.write_text("历史证据，不可覆盖", encoding="utf-8")
        # 回退 candidate 后再次 promote：应命中快照已存在而拒绝
        self.assertEqual(self._run("revert-candidate", "--baseline-id", bid)[0], 0)
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 1)
        self.assertIn("快照已存在", out)
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "历史证据，不可覆盖")

    def test_promote_rewrites_evidence_scope(self):
        # promote 透传 --evidence-scope：改写条目与 evidence 中的范围（如 no-code-change），
        # 收据内嵌打包证据（rc=0）→ 一致性校验推导 PASS 与基线一致仍放行
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        with self._patch_no_code_changes([]):
            rc, _ = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer",
                              "--evidence-scope", "no-code-change")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["evidence_scope"], "no-code-change")
        self.assertEqual(b["evidence"]["evidence_scope"], "no-code-change")

    # ── 方向 1（本批意图 1）：no-code-change 机器核对（Python 层对称防绕过）──
    def test_promote_no_code_change_blocks_on_code_changes(self):
        # 机器核对：直调 promote 伪造 no-code-change 但 code/ 有改动 → 拒并列提交
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        with self._patch_no_code_changes(["abc123 fix: 动过 code",
                                          "def456 新增(module): 又一个改动"]):
            rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer",
                                "--evidence-scope", "no-code-change")
        self.assertEqual(rc, 1)
        self.assertIn("机器核对", out)
        self.assertIn("code/ 实际 diff 不符", out)
        self.assertIn("abc123 fix: 动过 code", out)
        self.assertIn("def456 新增(module): 又一个改动", out)
        self.assertEqual(br.load()["baselines"][0]["status"], "candidate")

    def test_promote_no_code_change_fail_closed_without_origin(self):
        # 无法核对（缺 origin/main 引用 → git 失败返 None）按 fail-closed 拒绝豁免
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        with self._patch_no_code_changes(None):
            rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer",
                                "--evidence-scope", "no-code-change")
        self.assertEqual(rc, 1)
        self.assertIn("无法核对 code/ 改动", out)
        self.assertIn("拒绝 no-code-change 豁免", out)

    # ── 方向 2（本批意图 2）：promote 改写 evidence_scope 过收据 cases 子集校验 ──
    def test_promote_scope_rewrite_oversell_blocked(self):
        # 改写 scope 声称未实测用例范围（超出收据 cases）→ 拒（防晋升一步过度声称）
        rp = self._make_receipt_pkg(cases="lcview-liveness,lcview-transfer")
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid,
                            "--approved-by", "reviewer",
                            "--evidence-scope", "lcview-liveness,lcview-perf")
        self.assertEqual(rc, 1)
        self.assertIn("超出收据实测 cases", out)
        self.assertIn("lcview-perf", out)
        self.assertEqual(br.load()["baselines"][0]["status"], "candidate")

    def test_promote_scope_rewrite_subset_ok(self):
        # 改写 scope 为收据 cases 子集（收窄声明）→ 子集校验通过，全量覆盖晋升成功
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid,
                            "--approved-by", "reviewer",
                            "--evidence-scope", "lcview-transfer")
        self.assertEqual(rc, 0, out)
        self.assertIn("promoted:", out)
        self.assertEqual(br.load()["baselines"][0]["evidence_scope"],
                         "lcview-transfer")

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
                          blocking_reason="影响一致性", status="fixed", **base), "y")
        write_issue(Issue(issue_id="KI-OPEN-1", title="问题三",
                          origin="pre-existing", blocking=False,
                          status="open", **base), "z")

    def test_promote_writes_known_issues_closed_and_archives(self):
        # 方向 3（本批意图 3）：promote 归档不再删除——终态条目清单入
        # evidence.known_issues_closed，且归档进基线文档（证据快照）新增段落
        #（逐条 id/标题/修复提交）；文件全部保留（registry 不清零），index 不变
        from cdp_issue import issue_files, read_index
        self._write_issue_files()
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)
        b = br.load()["baselines"][0]
        # 清单入档为明细列表（含 resolved_in 与 title 与 archived_in）
        self.assertEqual(b["evidence"]["known_issues_closed"], [
            {"issue_id": "KI-CLOSE-1", "resolved_in": "abc123",
             "title": "问题一", "archived_in": ""},
            {"issue_id": "KI-CLOSE-2", "resolved_in": "",
             "title": "问题二", "archived_in": ""},
        ])
        # 归档段写入基线文档（快照 = 基线文档本体）
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        snap_text = snapshot.read_text(encoding="utf-8")
        self.assertIn("## 已修复问题归档", snap_text)
        self.assertIn("KI-CLOSE-1 | 问题一 | 修复提交: abc123", snap_text)
        self.assertIn("KI-CLOSE-2 | 问题二 | 修复提交: 未记", snap_text)
        # 文件全部保留（不再删终态），活项也在，index 覆盖全部
        issues_dir = self._root / "data" / "known-issues"
        remaining = {i.issue_id for p in issue_files(issues_dir)
                     for i in [br.read_issue(p)]}
        self.assertEqual(remaining, {"KI-CLOSE-1", "KI-CLOSE-2", "KI-OPEN-1"})
        self.assertEqual({e["issue_id"] for e in read_index(issues_dir)},
                         {"KI-CLOSE-1", "KI-CLOSE-2", "KI-OPEN-1"})

    def test_promote_archives_when_evidence_not_dict(self):
        # evidence 非字典写不成清单 → 告警，但归档仍入基线文档，文件保留
        self._write_issue_files()
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        b = br.load()["baselines"][0]
        data = br.load()
        data["baselines"][0]["evidence"] = "not-a-dict"
        br.save(data)
        bid = b["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 0)
        self.assertIn("evidence 非字典", out)
        self.assertIn("promoted:", out)
        # 归档段仍入基线文档（归档独立于 evidence 形态）
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        snap_text = snapshot.read_text(encoding="utf-8")
        self.assertIn("## 已修复问题归档", snap_text)
        self.assertIn("KI-CLOSE-1 | 问题一 | 修复提交: abc123", snap_text)
        # 终态文件保留，状态仍 promoted
        from cdp_issue import issue_files
        issues_dir = self._root / "data" / "known-issues"
        remaining = {i.issue_id for p in issue_files(issues_dir)
                     for i in [br.read_issue(p)]}
        self.assertEqual(remaining, {"KI-CLOSE-1", "KI-CLOSE-2", "KI-OPEN-1"})
        self.assertEqual(br.load()["baselines"][0]["status"], "promoted")

    def test_promote_no_closed_issues_snapshot_is_receipt_copy(self):
        # 无终态条目 → 不追加归档段：快照 = 收据原文（收据拷贝语义不变）
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, _ = self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")
        self.assertEqual(rc, 0)
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        self.assertTrue(snapshot.is_file(), "快照未落盘")
        self.assertEqual(snapshot.read_text(encoding="utf-8"),
                         Path(rp).read_text(encoding="utf-8"))

    def test_revert_candidate_requires_promoted(self):
        # 仅 promoted 可 revert-candidate：直接对 candidate revert 必须拒绝
        rp = self._make_receipt_pkg()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("revert-candidate", "--baseline-id", bid)
        self.assertEqual(rc, 1)
        self.assertIn("仅 promoted 可 revert-candidate", out)
        # promote 后再 revert 成功
        self.assertEqual(self._run("promote", "--baseline-id", bid, "--approved-by", "reviewer")[0], 0)
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

    @unittest.skipUnless(__import__("shutil").which("git"), "需要 git 解释器")
    def test_code_changes_since_main_ignores_main_side(self):
        # pub-04 红灯：两点语法 origin/main..HEAD——main 领先（main 侧有 code/
        # 提交而 dev 无）时不得把 main 侧提交算进 dev 改动（三点对称差曾把
        # main 侧提交误判为 dev 改动，令 no-code-change 豁免误拒）
        import subprocess
        repo = self._root / "repo"
        repo.mkdir()

        def _git(*args):
            return subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True,
                                  encoding="utf-8", check=True)

        _git("init", "-q")
        _git("symbolic-ref", "HEAD", "refs/heads/main")
        _git("config", "user.email", "t@t")
        _git("config", "user.name", "t")
        (repo / "code").mkdir()
        (repo / "code" / "a.txt").write_text("base\n", encoding="utf-8")
        _git("add", "-A")
        _git("commit", "-q", "-m", "base")
        _git("branch", "dev")
        main_sha = _git("rev-parse", "HEAD").stdout.strip()
        # 本地构造 origin/main 引用（无远端，直接写 remote-tracking ref）
        _git("update-ref", "refs/remotes/origin/main", main_sha)
        # main 领先：main 侧 code/ 提交（dev 不含）
        _git("checkout", "-q", "main")
        (repo / "code" / "m.txt").write_text("main\n", encoding="utf-8")
        _git("add", "-A")
        _git("commit", "-q", "-m", "main 侧 code 改动")
        _git("checkout", "-q", "dev")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            changes = br._code_changes_since_main()
        finally:
            os.chdir(cwd)
        self.assertEqual(changes, [])


class TestApprovalTokenNoStub(unittest.TestCase):
    """审批 token 不打桩例（方向 1：修路径 + 缺 token 判红）。

    既有 promote 用例全部 stub `_read_approval_token` 返预设值，路径多拼
    一层 harness 恒读空 + 空 expected 短路放行的 fail-open 被整体掩盖。
    本类直接测真实文件读取（不打桩）与缺预设判红行为。
    """

    def test_read_approval_token_real_file(self):
        # 不打桩：写真实 env 文件直读（回归值解析与引号剥离）
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "promote-approval.env"
            env.write_text('LC_PROMOTE_APPROVAL_TOKEN="tok-xyz"\n',
                           encoding="utf-8")
            self.assertEqual(br._read_approval_token(str(env)), "tok-xyz")

    def test_read_approval_token_strips_single_quotes(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "promote-approval.env"
            env.write_text("LC_PROMOTE_APPROVAL_TOKEN='tok-s'\n",
                           encoding="utf-8")
            self.assertEqual(br._read_approval_token(str(env)), "tok-s")

    def test_read_approval_token_missing_file_empty(self):
        self.assertEqual(br._read_approval_token("/nonexistent/x.env"), "")

    def test_check_missing_preset_token_rejects(self):
        # 缺预设判红（fail-open 修复核心）：预设空 → 必须拒（此前空 expected
        # 短路跳过比对，任意非空 token 放行）
        with mock.patch.object(br, "_read_approval_token", return_value=""):
            ok, err = br._check_approval_independence(
                "reviewer <r@x.com>", "lechao <lechao@x.com>", "any-token")
        self.assertFalse(ok)
        self.assertIn("promote-approval.env 缺失或未预设", err)

    def test_check_mismatch_rejects(self):
        with mock.patch.object(br, "_read_approval_token",
                               return_value="preset-tok"):
            ok, err = br._check_approval_independence(
                "reviewer", "lechao", "wrong-tok")
        self.assertFalse(ok)
        self.assertIn("不一致", err)

    def test_check_match_ok(self):
        with mock.patch.object(br, "_read_approval_token",
                               return_value="preset-tok"):
            ok, _ = br._check_approval_independence(
                "reviewer", "lechao", "preset-tok")
        self.assertTrue(ok)

    def test_check_token_file_override_reads_real_file(self):
        # --approval-token-file 透传：不打桩、真实文件读取判定
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "promote-approval.env"
            env.write_text("LC_PROMOTE_APPROVAL_TOKEN=ovr-tok\n",
                           encoding="utf-8")
            ok, _ = br._check_approval_independence(
                "reviewer", "lechao", "ovr-tok", str(env))
            self.assertTrue(ok)
            ok, err = br._check_approval_independence(
                "reviewer", "lechao", "wrong-tok", str(env))
            self.assertFalse(ok)
            self.assertIn("不一致", err)


if __name__ == "__main__":
    unittest.main()

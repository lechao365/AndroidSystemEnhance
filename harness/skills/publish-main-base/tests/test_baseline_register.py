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

    def _make_receipt(self, build="pass", board="fail", cases="lcview-liveness"):
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
        # candidate 必须实读收据：build/package=build、board_verify=push_board，均大写
        rp = self._make_receipt(build="pass", board="fail")
        rc, out = self._run("add-candidate", "--receipt-path", rp,
                            "--source-commit", "abc123",
                            "--evidence-scope", "lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertIn("candidate:", out)
        b = br.load()["baselines"][0]
        self.assertEqual(b["status"], "candidate")
        self.assertEqual(b["build_result"], "PASS")
        self.assertEqual(b["package_result"], "PASS")
        self.assertEqual(b["board_verify"], "FAIL")
        self.assertEqual(b["evidence"]["build_result"], "PASS")
        self.assertEqual(b["evidence"]["package_result"], "PASS")
        self.assertEqual(b["evidence"]["board_verify"], "FAIL")
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
        rp1 = self._make_receipt(build="pass", board="fail")
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

    def test_promote_requires_candidate(self):
        # 非 candidate 状态 promote 必须拒绝（门禁可信）
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        self.assertEqual(self._run("promote", "--baseline-id", bid)[0], 0)
        rc, out = self._run("promote", "--baseline-id", bid)
        self.assertEqual(rc, 1)
        self.assertIn("仅 candidate 可 promote", out)

    def test_promote_creates_evidence_snapshot(self):
        # promote 落盘证据快照：data/baselines/<id>-<收据名>.md，内容与收据一致
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, out = self._run("promote", "--baseline-id", bid)
        self.assertEqual(rc, 0)
        self.assertIn("promoted:", out)
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        self.assertTrue(snapshot.is_file(), f"快照未落盘: {snapshot}")
        self.assertEqual(snapshot.read_text(encoding="utf-8"),
                         Path(rp).read_text(encoding="utf-8"))

    def test_promote_duplicate_snapshot_rejected(self):
        # 重复 promote（revert 后再 promote）：快照已存在即拒，不得覆盖历史证据
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        self.assertEqual(self._run("promote", "--baseline-id", bid)[0], 0)
        snapshot = self._root / "data" / "baselines" / f"{bid}-{Path(rp).name}"
        snapshot.write_text("历史证据，不可覆盖", encoding="utf-8")
        # 回退 candidate 后再次 promote：应命中快照已存在而拒绝
        self.assertEqual(self._run("revert-candidate", "--baseline-id", bid)[0], 0)
        rc, out = self._run("promote", "--baseline-id", bid)
        self.assertEqual(rc, 1)
        self.assertIn("快照已存在", out)
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "历史证据，不可覆盖")

    def test_promote_rewrites_evidence_scope(self):
        # promote 透传 --evidence-scope：改写条目与 evidence 中的范围（如 no-code-change）
        rp = self._make_receipt()
        self.assertEqual(self._run("add-candidate", "--receipt-path", rp,
                                   "--evidence-scope", "lcview-liveness")[0], 0)
        bid = br.load()["baselines"][0]["baseline_id"]
        rc, _ = self._run("promote", "--baseline-id", bid,
                          "--evidence-scope", "no-code-change")
        self.assertEqual(rc, 0)
        b = br.load()["baselines"][0]
        self.assertEqual(b["evidence_scope"], "no-code-change")
        self.assertEqual(b["evidence"]["evidence_scope"], "no-code-change")

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
        self.assertEqual(self._run("promote", "--baseline-id", bid)[0], 0)
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

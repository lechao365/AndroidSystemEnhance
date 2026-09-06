"""ws_session 单元测试（全离线，不依赖设备与真实批次）。"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_session


class TestFingerprint(unittest.TestCase):
    def test_normalize_strips_volatile(self):
        # 时间戳/绝对路径/十六进制地址归一化；数字保留（_NUM_RE 已删，
        # 数值是语义稳定部分——端口/行号/计数不同即不同问题，不过激打码）
        a = ws_session.normalize_error_line(
            "08-30 10:00:00.123 /home/u/ws/out err 0xdeadbeef size=42")
        b = ws_session.normalize_error_line(
            "08-30 11:20:33.999 /home/u/ws/out err 0x1234abcd size=42")
        self.assertEqual(a, b)

        # 数字不再归一化：同为"size=42"才等指纹，数值差异保留（过激归一化
        # 会把不同问题折叠成同一指纹而误判"指纹冻结"）
        c = ws_session.normalize_error_line(
            "08-30 12:00:00.000 /home/u/ws/out err 0xbeef size=7")
        self.assertNotEqual(a, c)

        # logcat 首错误行（MM-DD HH:MM:SS.mmm）：时间戳必须走 <TS> 路径归一化，
        # 而非仅靠数字打码，避免 _NUM_RE 削弱后时间戳方差破坏指纹稳定
        lc1 = ws_session.normalize_error_line(
            "08-30 10:20:15.123 LcView: heartbeat fail count=42")
        lc2 = ws_session.normalize_error_line(
            "08-30 11:22:33.456 LcView: heartbeat fail count=42")
        self.assertEqual(lc1, lc2)
        self.assertIn("<TS>", lc1)
        self.assertNotIn("10:20:15.123", lc1)
        # 数字保留：count=42 原样留在指纹中（时间戳已打码，其余语义保留）
        self.assertIn("count=42", lc1)

    def test_normalize_keeps_semantics(self):
        # 错误类别与稳定消息不同 -> 归一化结果不同
        a = ws_session.normalize_error_line("logcat 未命中 关键字 heartbeat")
        b = ws_session.normalize_error_line("logcat 未命中 关键字 monitor")
        self.assertNotEqual(a, b)

    def test_compute_fingerprint_stable_and_short(self):
        fp1 = ws_session.compute_fingerprint("build", 1, "error X 0x00")
        fp2 = ws_session.compute_fingerprint("build", 1, "error X 0xff")
        fp3 = ws_session.compute_fingerprint("sync", 1, "error X 0x00")
        self.assertEqual(fp1, fp2)          # 归一化后同指纹
        self.assertNotEqual(fp1, fp3)       # 阶段不同 -> 不同指纹
        self.assertEqual(len(fp1), 12)      # 12 位 hex


class TestSessionCore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._root = Path(self._tmp.name) / "harness" / "log" / "loop-engineering"

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def test_create_and_load(self):
        s = ws_session.create_session(
            goal="验证 liveness", target="dev", case="lcview-liveness")
        self.assertEqual(s["patience"], 0)
        self.assertEqual(s["total_attempts"], 0)
        self.assertEqual(s["max_patience"], 3)
        self.assertEqual(s["max_total"], 10)
        self.assertEqual(s["mode"], "B")
        self.assertIsNone(s["exit_attribution"])
        path = ws_session.save_session(s)
        self.assertTrue(path.is_file())
        loaded = ws_session.load_session(path)
        self.assertEqual(loaded["id"], s["id"])
        self.assertEqual(loaded["goal"], "验证 liveness")

    def test_load_invalid(self):
        bad = self._root / "session-x" / "session.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ws_session.SessionError):
            ws_session.load_session(bad)

    def test_find_active_reuse(self):
        # 幂等：同 goal+target 的活跃会话被复用，不新建
        s1 = ws_session.create_session(goal="g1", target="dev", case="c")
        ws_session.save_session(s1)
        s2, reused = ws_session.find_active_session(goal="g1", target="dev")
        self.assertTrue(reused)
        self.assertEqual(s2["id"], s1["id"])
        s1["exit_attribution"] = "pass"
        ws_session.save_session(s1)
        s3, reused3 = ws_session.find_active_session(goal="g1", target="dev")
        self.assertFalse(reused3)   # 已终结 -> 不复用
        self.assertNotEqual(s3["id"], s1["id"])

    def test_save_session_custom_path(self):
        # 显式 path：文件落位且可完整往返
        s = ws_session.create_session(goal="g", batch_file="b.cdp")
        out = Path(self._tmp.name) / "custom" / "sub" / "s.json"
        ret = ws_session.save_session(s, path=out)
        self.assertEqual(ret, out)
        self.assertTrue(out.is_file())
        loaded = ws_session.load_session(out)
        self.assertEqual(loaded["id"], s["id"])
        self.assertEqual(loaded["goal"], "g")

    def test_save_session_atomic_no_tmp_residue(self):
        # 原子写后不残留 .tmp 中间文件
        s = ws_session.create_session(goal="g", target="dev")
        path = ws_session.save_session(s)
        self.assertFalse(path.with_suffix(".tmp").exists())
        # 二次覆盖保存同样无残留
        s["patience"] = 1
        ws_session.save_session(s, path=path)
        self.assertFalse(path.with_suffix(".tmp").exists())

    def test_load_invalid_forms(self):
        root = self._root
        # 文件缺失
        missing = root / "nope" / "session.json"
        with self.assertRaises(ws_session.SessionError):
            ws_session.load_session(missing)
        # 合法 JSON 但非对象
        arr = root / "session-a" / "session.json"
        arr.parent.mkdir(parents=True)
        arr.write_text("[1,2,3]", encoding="utf-8")
        with self.assertRaises(ws_session.SessionError):
            ws_session.load_session(arr)
        # 缺 runs 字段
        no_runs = root / "session-b" / "session.json"
        no_runs.parent.mkdir(parents=True)
        no_runs.write_text(json.dumps({"id": "x"}), encoding="utf-8")
        with self.assertRaises(ws_session.SessionError):
            ws_session.load_session(no_runs)
        # runs 非列表
        bad_runs = root / "session-c" / "session.json"
        bad_runs.parent.mkdir(parents=True)
        bad_runs.write_text(json.dumps({"id": "x", "runs": "notalist"}),
                            encoding="utf-8")
        with self.assertRaises(ws_session.SessionError):
            ws_session.load_session(bad_runs)
        # 缺必需计数/退出字段（Important 1 回归：不得放行裸 KeyError 源）
        missing_attr = {
            "id": "x", "patience": 0, "total_attempts": 0, "max_patience": 3,
            "max_total": 10, "goal": "g", "mode": "B", "runs": [],
        }
        fp = root / "session-bad-missattr" / "session.json"
        fp.parent.mkdir(parents=True)
        fp.write_text(json.dumps(missing_attr), encoding="utf-8")
        with self.assertRaises(ws_session.SessionError):
            ws_session.load_session(fp)
        # patience 类型错误（字符串）也不得放行
        bad_type = dict(missing_attr, exit_attribution=None, patience="0")
        fp2 = root / "session-bad-patience" / "session.json"
        fp2.parent.mkdir(parents=True)
        fp2.write_text(json.dumps(bad_type), encoding="utf-8")
        with self.assertRaises(ws_session.SessionError):
            ws_session.load_session(fp2)

    def test_create_session_mode_a(self):
        # 模式 A：传 batch_file 即 A
        s = ws_session.create_session(goal="验证 CDP", batch_file="b.cdp")
        self.assertEqual(s["mode"], "A")
        self.assertEqual(s["batch_file"], "b.cdp")
        self.assertEqual(s["target"], "")

    def test_find_active_skips_corrupt_dir(self):
        # 损坏目录被跳过：不崩溃，返回全新会话且不复用
        corrupt = self._root / "session-corrupt" / "session.json"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("{bad json", encoding="utf-8")
        s, reused = ws_session.find_active_session(goal="g2", target="dev")
        self.assertFalse(reused)
        self.assertEqual(s["goal"], "g2")


class TestDoneLogic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        from cdp_receipt import Receipt, write_receipt
        self._Receipt = Receipt
        self._write_receipt = write_receipt
        self._s = ws_session.create_session(goal="g", target="dev", case="c")

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def _receipt(self, result, acceptance=""):
        acc = acceptance or ('{"overall": "fail", "items": ['
                             '{"tag": "svc:lechao_lcview", "status": "fail", '
                             '"detail": "init.svc.x=stopped"}]}')
        # 文件名 <秒时间戳>-<batch_id>.md：batch_id 须每份唯一，
        # 否则同秒内多份收据相互覆盖（潜在 flake，spec §4.1）
        return self._write_receipt(
            self._Receipt(batch_id=f"b{uuid.uuid4().hex[:6]}", result=result,
                          build="pass" if result == "pass" else "fail",
                          push_board="pass" if result == "pass" else "fail",
                          acceptance=acc, summary="t"),
            "body")

    def test_done_pass_exits(self):
        rp = self._receipt("pass", acceptance='{"overall":"pass","items":[]}')
        s, guidance = ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertEqual(s["exit_attribution"], "pass")
        self.assertEqual(s["total_attempts"], 1)
        self.assertEqual(s["runs"][0]["attribution"], "pass")
        self.assertIn("终结", guidance)

    def test_done_fail_evolving_resets_patience(self):
        # 两次不同指纹（不同首错误行）-> patience 清零，不退出
        rp1 = self._receipt("fail", acceptance='{"items":[{"status":"fail","detail":"logcat 未命中 heartbeat"}]}')
        rp2 = self._receipt("fail", acceptance='{"items":[{"status":"fail","detail":"logcat 未命中 monitor"}]}')
        s, _ = ws_session.apply_done(self._s, rp1, stage="acceptance",
                                     error_line="logcat 未命中 heartbeat")
        s, g = ws_session.apply_done(s, rp2, stage="acceptance",
                                     error_line="logcat 未命中 monitor")
        self.assertEqual(s["patience"], 0)
        self.assertIsNone(s["exit_attribution"])
        self.assertIn("继续", g)
        self.assertEqual(s["total_attempts"], 2)

    def test_done_fail_frozen_accumulates_and_exits(self):
        # 同指纹连续冻结才累计（spec §4.2：首遇新指纹仅 total+1）：
        # 首轮观测 patience=0 + 连续 3 次冻结 -> patience=3 达上限 -> task_unsolvable
        rp = self._receipt("fail", acceptance='{"items":[{"status":"fail","detail":"stopped"}]}')
        s = self._s
        for _ in range(4):
            s, guidance = ws_session.apply_done(s, rp, stage="acceptance",
                                                error_line="init.svc.x=stopped")
        self.assertEqual(s["patience"], 3)
        self.assertEqual(s["exit_attribution"], "task_unsolvable")
        self.assertEqual(s["total_attempts"], 4)
        # 快照冗余已入 runs（抵抗收据老化）
        self.assertEqual(s["runs"][0]["snapshot"]["build"], "fail")
        # 首轮为观测（无前序失败）非冻结；第 2/3/4 轮指纹冻结
        self.assertFalse(s["runs"][0]["fingerprint_frozen"])
        self.assertTrue(all(r["fingerprint_frozen"] for r in s["runs"][1:]))

    def test_done_env_fail_does_not_burn_patience(self):
        # 冻结态下 env_fail 不烧修复轮：先累计 patience=1，再 env_fail 即刻终结
        rp = self._receipt("fail")
        s, _ = ws_session.apply_done(self._s, rp, stage="acceptance",
                                     error_line="same error")
        self.assertEqual(s["patience"], 0)  # 首轮观测
        s, _ = ws_session.apply_done(s, rp, stage="acceptance",
                                     error_line="same error")
        self.assertEqual(s["patience"], 1)  # 冻结累计
        rp2 = self._receipt("fail")
        s, guidance = ws_session.apply_done(s, rp2, stage="acceptance",
                                            attribution="env_fail")
        self.assertEqual(s["exit_attribution"], "env_fail")
        self.assertEqual(s["patience"], 1)  # env_fail 不烧修复轮

    def test_done_invalid_attribution_raises(self):
        # attribution 仅允许 env_fail/framework_error；非法值必须抛错
        rp = self._receipt("fail")
        for bad in ("bogus", "pass", "task_fail"):
            with self.assertRaises(RuntimeError):
                ws_session.apply_done(self._s, rp, attribution=bad)

    def test_done_framework_error_stops(self):
        rp = self._receipt("fail")
        s, guidance = ws_session.apply_done(self._s, rp, attribution="framework_error")
        self.assertEqual(s["exit_attribution"], "framework_error")
        self.assertIn("framework_error", guidance)

    def test_done_total_cap(self):
        # 指纹每轮演化但 total 达上限 -> cost_cap_exceeded
        s = ws_session.create_session(goal="g", target="dev", case="c",
                                      max_total=2)
        for i in range(2):
            rp = self._receipt("fail", acceptance=(
                '{"items":[{"status":"fail","detail":"err line %d"}]}' % i))
            s, _ = ws_session.apply_done(s, rp, stage="build",
                                         error_line=f"err line {i}")
        self.assertEqual(s["exit_attribution"], "cost_cap_exceeded")
        self.assertEqual(s["total_attempts"], 2)

    def test_done_bad_receipt_raises(self):
        with self.assertRaises(RuntimeError):
            ws_session.apply_done(self._s, "/nonexistent/receipt.md")

    def test_done_receipt_parse_errors_rejected(self):
        # 方向 2（损坏收据消费口径）：收据头部解析有错（schema_version 非 1）
        # → 拒绝记账，不得据损坏收据记 pass 推进会话/终态 pass
        rp = self._receipt("pass", acceptance='{"overall":"pass","items":[]}')
        text = Path(rp).read_text(encoding="utf-8").replace(
            "- schema_version: 1", "- schema_version: 99")
        Path(rp).write_text(text, encoding="utf-8")
        with self.assertRaises(RuntimeError) as cm:
            ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertIn("解析有错", str(cm.exception))
        self.assertIsNone(self._s["exit_attribution"])
        self.assertEqual(self._s["total_attempts"], 0)

    def test_done_pass_acceptance_overall_fail_rejected(self):
        # 收据 result=pass 但 acceptance overall=fail → 拒记账（防手填假绿
        # 推进会话/终态 pass）
        rp = self._receipt("pass", acceptance=(
            '{"overall":"fail","items":[{"tag":"svc:x","status":"fail",'
            '"detail":"stopped"}]}'))
        with self.assertRaises(RuntimeError) as cm:
            ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertIn("验收未通过", str(cm.exception))
        self.assertIn("overall 非 pass", str(cm.exception))
        self.assertIsNone(self._s["exit_attribution"])
        self.assertEqual(self._s["total_attempts"], 0)

    def test_done_pass_acceptance_non_json_rejected(self):
        # result=pass 而 acceptance 非 JSON（如手填 "ok"）→ 拒记账
        rp = self._receipt("pass", acceptance="手填 ok")
        with self.assertRaises(RuntimeError) as cm:
            ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertIn("非合法 JSON", str(cm.exception))

    def test_done_pass_acceptance_fail_item_rejected(self):
        # overall=pass 但 items 含 fail 项（自相矛盾）→ 拒记账
        rp = self._receipt("pass", acceptance=(
            '{"overall":"pass","items":[{"status":"fail","detail":"x"}]}'))
        with self.assertRaises(RuntimeError) as cm:
            ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertIn("含 fail 项", str(cm.exception))

    def test_done_pass_acceptance_empty_rejected(self):
        # result=pass 而 acceptance 为空（纯空白）→ 拒记账
        rp = self._receipt("pass", acceptance="   ")
        with self.assertRaises(RuntimeError) as cm:
            ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertIn("acceptance 为空", str(cm.exception))

    def test_done_pass_acceptance_array_format_ok(self):
        # 历史数组格式（无 overall）全 pass → 兼容放行（有逐项证据即真绿）
        rp = self._receipt("pass", acceptance=(
            '[{"tag":"svc:x","status":"pass","detail":"running"}]'))
        s, guidance = ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertEqual(s["exit_attribution"], "pass")
        self.assertIn("终结", guidance)

    def test_done_pass_acceptance_array_with_fail_rejected(self):
        # 数组格式含 fail 项 → 拒记账
        rp = self._receipt("pass", acceptance=(
            '[{"tag":"svc:x","status":"fail","detail":"stopped"}]'))
        with self.assertRaises(RuntimeError) as cm:
            ws_session.apply_done(self._s, rp, stage="acceptance")
        self.assertIn("含 fail 项", str(cm.exception))

    def test_extract_first_fail(self):
        line = ws_session.extract_first_fail_line(
            '{"items":[{"status":"fail","detail":"first bad"},'
            '{"status":"fail","detail":"second bad"}]}')
        self.assertEqual(line, "first bad")
        self.assertEqual(ws_session.extract_first_fail_line(""), "")
        # 非 JSON 非空：返回原文首行（仍作指纹源）
        self.assertEqual(
            ws_session.extract_first_fail_line("plain error first\nsecond"),
            "plain error first")
        # 纯空白：视为空，返回空串
        self.assertEqual(ws_session.extract_first_fail_line("   "), "")


class TestPruneSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._root = ws_session.sessions_root()
        self._keep = ws_session._SESSION_KEEP

    def tearDown(self):
        ws_session._SESSION_KEEP = self._keep
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def _mk(self, i, finished):
        s = ws_session.create_session(goal=f"g{i}", target="dev")
        if finished:
            s["exit_attribution"] = "pass"
        ws_session.save_session(s)

    def test_prune_removes_oldest_finished_only(self):
        ws_session._SESSION_KEEP = 3
        for i in range(6):        # 0..2 已终结（旧），3..5 活跃
            self._mk(i, finished=i < 3)
        # 删除前快照已终结目录（物理删除后无法回读，快照比对是唯一可靠判定）
        finished_before = {str(d) for d in self._root.glob("session-*")
                           if ws_session.load_session(d / "session.json")
                           .get("exit_attribution")}
        removed = ws_session.prune_sessions()
        self.assertEqual(len(removed), 3)   # 总数 6 -> 配额 3：删 3 个最旧已终结
        # 被删的 3 个目录必须都是已终结会话（快照比对）
        self.assertEqual(set(removed), finished_before)
        # 活跃目录（后 3 个）仍在
        alive = [d for d in self._root.glob("session-*")]
        self.assertEqual(len(alive), 3)
        for d in alive:
            s = ws_session.load_session(d / "session.json")
            self.assertIsNone(s["exit_attribution"])

    def test_prune_all_active_keeps_and_warns(self):
        import contextlib
        import io as _io
        ws_session._SESSION_KEEP = 1
        for i in range(3):
            self._mk(i, finished=False)   # 全活跃
        err = _io.StringIO()
        with contextlib.redirect_stderr(err):
            removed = ws_session.prune_sessions()
        self.assertEqual(removed, [])
        self.assertEqual(len(list(self._root.glob("session-*"))), 3)
        self.assertIn("WARN", err.getvalue())

    def test_prune_keeps_newest_finished(self):
        # 已终结数 > 配额：必须删最旧，保留最新已终结（顺序判定回归点）
        ws_session._SESSION_KEEP = 2
        for i in range(6):
            self._mk(i, finished=True)
        # 强制 distinct created_at（秒级粒度在循环内可能并列），确定顺序
        import json as _json
        base = "2026-08-30T09:{:02d}:00"
        for idx, d in enumerate(sorted(self._root.glob("session-*"))):
            sj = d / "session.json"
            data = _json.loads(sj.read_text(encoding="utf-8"))
            data["created_at"] = base.format(idx)
            data["updated_at"] = data["created_at"]
            sj.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
            # 目录 mtime 拉平：ext4/tmpfs 粒度细于 drvfs，不拉平时 mtime
            # 主导排序键、created_at 兜底不可达（review 原版测试在本机 flake）
            os.utime(d, (1750000000.0, 1750000000.0))
        # 删除前快照全部已终结的 created_at（删除后无法回读）
        before = sorted(
            _json.loads((d / "session.json").read_text(encoding="utf-8"))["created_at"]
            for d in self._root.glob("session-*"))
        removed = ws_session.prune_sessions()
        self.assertEqual(len(removed), 4)   # 6 - 2 配额
        survivors = [ws_session.load_session(d / "session.json")
                     for d in self._root.glob("session-*")]
        self.assertEqual(len(survivors), 2)
        # 幸存者 created_at == 全局最新两个
        self.assertEqual(sorted(s["created_at"] for s in survivors), before[-2:])

    def test_prune_corrupt_dir_skipped(self):
        # 损坏 session.json 目录保守跳过不删
        ws_session._SESSION_KEEP = 1
        self._mk(0, finished=False)
        bad = self._root / "session-bad"
        bad.mkdir(parents=True)
        (bad / "session.json").write_text("{bad", encoding="utf-8")
        removed = ws_session.prune_sessions()
        self.assertEqual(removed, [])
        self.assertTrue(bad.exists())


class TestDiagnosis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def test_diagnosis_content(self):
        s = ws_session.create_session(goal="验证守恒", target="dev")
        s["runs"] = [
            {"attempt": 1, "ran_at": "t1", "receipt_path": "r1.md",
             "result": "fail", "stage": "build", "verify_exit": 1,
             "fingerprint": "abc", "fingerprint_frozen": False,
             "attribution": "task_fail", "fix_action": "修 include",
             "log": "", "snapshot": {"build": "fail", "push_board": "skip",
                                     "acceptance_first_line": "", "summary": ""}},
            {"attempt": 2, "ran_at": "t2", "receipt_path": "r2.md",
             "result": "fail", "stage": "build", "verify_exit": 1,
             "fingerprint": "abc", "fingerprint_frozen": True,
             "attribution": "task_fail", "fix_action": "再修链接",
             "log": "", "snapshot": {"build": "fail", "push_board": "skip",
                                     "acceptance_first_line": "", "summary": ""}},
        ]
        s["patience"], s["total_attempts"] = 2, 2
        s["exit_attribution"] = "task_unsolvable"
        md = ws_session.build_diagnosis(s)
        self.assertIn("归因: task_unsolvable", md)
        self.assertIn("attempt", md)
        self.assertIn("修 include", md)          # 各轮修复动作
        self.assertIn("已证伪修复方向", md)      # AI 补充段骨架
        self.assertIn("r2.md", md)               # 收据路径可追溯
        self.assertIn("(冻结)", md)              # 指纹演化轨迹冻结标记（run2 冻结）
        # 快照列渲染：build/board 取自 snapshot（收据老化后仍自洽，spec §4.1）
        self.assertIn("| fail | skip | r2.md |", md)

    def test_diagnosis_unfinished(self):
        s = ws_session.create_session(goal="g", target="dev")
        md = ws_session.build_diagnosis(s)
        self.assertIn("归因: 未终结", md)
        # 无轮次时指纹链回退「（无）」，不产出空轨迹
        self.assertIn("（无）", md)

    def test_diagnosis_escapes_pipe_in_cells(self):
        # fix_action 含 | 须转义为 \|，防止破坏表格列结构
        s = ws_session.create_session(goal="g", target="dev")
        s["runs"] = [
            {"attempt": 1, "ran_at": "t1", "receipt_path": "r1.md",
             "result": "fail", "stage": "build", "verify_exit": 1,
             "fingerprint": "abc", "fingerprint_frozen": False,
             "attribution": "task_fail", "fix_action": "改 src|obj 路径",
             "log": "", "snapshot": {"build": "fail", "push_board": "skip",
                                     "acceptance_first_line": "", "summary": ""}},
        ]
        s["total_attempts"], s["exit_attribution"] = 1, "task_unsolvable"
        md = ws_session.build_diagnosis(s)
        self.assertIn("改 src\\|obj 路径", md)   # 管道转义后单元格内容保留
        self.assertNotIn("改 src|obj 路径", md)  # 未转义管道不得出现在行内


VALID_CDP = """-sv base:1a2b3c4d5e6f
意图: 验证 liveness 判据
验收: svc:lechao_lcview boot
方向: 确认服务存活
"""


class TestRunStatus(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        # verify-cases.yaml 资产桩（run 指引模式 B 解析 --case）
        cfg = Path(self._tmp.name) / "harness" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "verify-cases.yaml").write_text(
            "cases:\n  lcview-liveness: 'svc:lechao_lcview boot'\n",
            encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def _batch_file(self):
        f = Path(self._tmp.name) / "b.cdp"
        f.write_text(VALID_CDP, encoding="utf-8")
        return str(f)

    def test_run_guidance_mode_a(self):
        s = ws_session.create_session(goal="g", batch_file=self._batch_file())
        text = ws_session.run_guidance(s)
        self.assertIn("ws_upload_tests.py", text)       # 上板真跑 C++ 单测步骤
        self.assertIn("unit_test", text)                # done --stage 含 unit_test
        self.assertIn("ws_acceptance.py run --batch-file", text)
        self.assertIn("svc:lechao_lcview boot", text)   # 批次验收已解析
        self.assertIn("--target 1a2b3c4d5e6f", text)    # 模式 A --target 取批次 base
        self.assertIn("ws_report.py", text)
        self.assertIn("done --session", text)
        # 方向 4：先产自描述产物再传给报告——单测/验收产物落会话日志目录，
        # PASS 经 --acceptance-file/--unit-test-file 按产物核验
        self.assertIn(f"session-{s['id']}", text)
        self.assertIn("--result-file", text)
        self.assertIn("--acceptance-file", text)
        self.assertIn("--unit-test-file", text)

    def test_run_guidance_mode_a_empty_acceptance(self):
        # 模式 A 批次验收为「无」：须拒绝（-sv 批次须有验收）
        f = Path(self._tmp.name) / "b-empty.cdp"
        f.write_text(VALID_CDP.replace("验收: svc:lechao_lcview boot", "验收: 无"),
                     encoding="utf-8")
        s = ws_session.create_session(goal="g", batch_file=str(f))
        with self.assertRaises(RuntimeError):
            ws_session.run_guidance(s)

    def test_run_guidance_missing_source(self):
        # 会话既无 batch_file 也无 case：验收源缺失须报错
        s = ws_session.create_session(goal="g")
        with self.assertRaises(RuntimeError):
            ws_session.run_guidance(s)

    def test_run_guidance_mode_b(self):
        s = ws_session.create_session(goal="g", target="dev",
                                      case="lcview-liveness")
        text = ws_session.run_guidance(s)
        self.assertIn("--case lcview-liveness", text)
        self.assertIn("--target dev", text)

    def test_run_guidance_bad_case(self):
        s = ws_session.create_session(goal="g", target="dev", case="no-such")
        with self.assertRaises(RuntimeError):
            ws_session.run_guidance(s)

    def test_status_text(self):
        s = ws_session.create_session(goal="g", target="dev")
        s["runs"] = [{"attempt": 1, "ran_at": "t", "receipt_path": "r.md",
                      "result": "fail", "stage": "build", "verify_exit": 1,
                      "fingerprint": "aa", "fingerprint_frozen": False,
                      "attribution": "task_fail", "fix_action": "",
                      "log": "", "snapshot": {}}]
        s["total_attempts"], s["patience"] = 1, 0
        text = ws_session.status_text(s)
        self.assertIn("patience 0/3", text)
        self.assertIn("total 1/10", text)
        self.assertIn("task_fail", text)
        self.assertIn("下一轮", text)

    def test_status_text_terminal(self):
        # 终结会话：状态含终结，且不出现双重「下一步:」前缀
        s = ws_session.create_session(goal="g", target="dev")
        s["total_attempts"], s["patience"] = 3, 3
        s["exit_attribution"] = "task_unsolvable"
        text = ws_session.status_text(s)
        self.assertIn("终结", text)
        self.assertNotIn("下一步: 会话终结", text)   # 旧 bug：终结指引被叠加前缀
        self.assertIn("下一步: diagnose", text)

    def test_run_guidance_terminal_raises(self):
        # 已终结会话不得再生成新轮指引
        s = ws_session.create_session(goal="g", target="dev",
                                      case="lcview-liveness")
        s["exit_attribution"] = "pass"
        with self.assertRaises(RuntimeError):
            ws_session.run_guidance(s)


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        cfg = Path(self._tmp.name) / "harness" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "verify-cases.yaml").write_text(
            "cases:\n  lcview-liveness: 'svc:lechao_lcview boot'\n",
            encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def test_start_mode_b_and_reuse(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_session.main(["start", "--goal", "验证存活",
                                  "--target", "dev", "--case", "lcview-liveness"])
        self.assertEqual(rc, 0)
        m = re.search(r"session: (.+)", buf.getvalue())
        self.assertTrue(m)
        sj = m.group(1)
        # 幂等复用
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = ws_session.main(["start", "--goal", "验证存活",
                                   "--target", "dev", "--case", "lcview-liveness"])
        self.assertEqual(rc2, 0)
        self.assertIn("复用", buf2.getvalue())
        self.assertIn(sj, buf2.getvalue())

    def test_start_mutex_error(self):
        rc = ws_session.main(["start", "--goal", "g"])
        self.assertEqual(rc, 2)

    def test_run_bad_case_returns_1(self):
        # 错误路径回归：坏 case 标签触发 run_guidance RuntimeError，
        # CLI 须转退出码 1 + error 输出，不得裸 traceback 逃逸（spec §9）
        import contextlib
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_session.main(["start", "--goal", "坏标签", "--target",
                                  "dev", "--case", "no-such"])
        sj = re.search(r"session: (.+)", buf.getvalue()).group(1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc_run = ws_session.main(["run", "--session", sj])
        self.assertEqual(rc_run, 1)
        self.assertIn("error:", err.getvalue())
        self.assertIn("no-such", err.getvalue())

    def test_reuse_keeps_original_case(self):
        # 复用活跃会话不得覆盖既有 case（防同 run 历史混两种验收标准）
        import contextlib
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_session.main(["start", "--goal", "复用保原", "--target",
                                  "dev", "--case", "lcview-liveness"])
        self.assertEqual(rc, 0)
        sj = re.search(r"session: (.+)", buf.getvalue()).group(1)
        err = io.StringIO()
        buf2 = io.StringIO()
        with redirect_stdout(buf2), contextlib.redirect_stderr(err):
            rc2 = ws_session.main(["start", "--goal", "复用保原", "--target",
                                   "dev", "--case", "other"])
        self.assertEqual(rc2, 0)
        self.assertIn("复用", buf2.getvalue())
        self.assertIn("WARN", err.getvalue())
        self.assertIn("other", err.getvalue())
        s = ws_session.load_session(sj)
        self.assertEqual(s["case"], "lcview-liveness")

    def test_diagnose_writes_session_dir(self):
        # 诊断落位会话目录（确定性），非 --session 裸文件名所在 CWD
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_session.main(["start", "--goal", "诊断落位", "--target",
                                  "dev", "--case", "lcview-liveness"])
        sj = re.search(r"session: (.+)", buf.getvalue()).group(1)
        s = ws_session.load_session(sj)
        out = ws_session.sessions_root() / f"session-{s['id']}" / "diagnosis.md"
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc_diag = ws_session.main(["diagnose", "--session", sj])
        self.assertEqual(rc_diag, 0)
        self.assertTrue(out.is_file())
        self.assertIn("归因", out.read_text(encoding="utf-8"))

    def test_status_on_missing(self):
        rc = ws_session.main(["status", "--session", "/nonexistent.json"])
        self.assertEqual(rc, 3)

    def test_mode_b_closed_loop_offline(self):
        # 模式 B 全闭环（离线）：start -> run(指引) -> done(pass) -> status 终结
        from cdp_receipt import Receipt, write_receipt
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_session.main(["start", "--goal", "闭环", "--target", "dev",
                                  "--case", "lcview-liveness"])
        sj = re.search(r"session: (.+)", buf.getvalue()).group(1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ws_session.main(["run", "--session", sj]), 0)
        rp = write_receipt(Receipt(batch_id="cb", result="pass", build="pass",
                                   push_board="pass",
                                   acceptance='{"overall":"pass","items":[]}',
                                   summary="ok"),
                           "pass body")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                ws_session.main(["done", "--session", sj, "--receipt", str(rp),
                                 "--stage", "acceptance"]), 0)
        buf3 = io.StringIO()
        with redirect_stdout(buf3):
            self.assertEqual(ws_session.main(["status", "--session", sj]), 0)
        self.assertIn("pass", buf3.getvalue())
        s = ws_session.load_session(sj)
        self.assertEqual(s["exit_attribution"], "pass")

    def test_mode_b_independence(self):
        # 独立性断言（spec §5）：不引用 apply 侧任何组件
        src = Path(ws_session.__file__).read_text(encoding="utf-8")
        for banned in ("cross-device-apply", "git-works-push",
                       "sync-code-to-workspace", "sync-workspace-to-code"):
            self.assertNotIn(banned, src)
        # 允许且仅允许共享库：cross-device/lib/python（cdp_receipt/cdp_parse/cdp_paths）
        self.assertIn("cross-device/lib/python", src)


class TestRunStateDone(unittest.TestCase):
    """方向 5：done --run-file 取链式运行态 stage/rc，替换 AI 代理值。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        from cdp_receipt import Receipt, write_receipt
        self._Receipt = Receipt
        self._write_receipt = write_receipt
        self._s = ws_session.create_session(goal="g", target="dev", case="c")

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def _receipt(self, result):
        acc = ('{"overall":"pass","items":[]}' if result == "pass"
               else '{"items":[{"status":"fail","detail":"init.svc.x=stopped"}]}')
        return self._write_receipt(
            self._Receipt(batch_id=f"b{uuid.uuid4().hex[:6]}", result=result,
                          build="pass" if result == "pass" else "fail",
                          push_board="pass" if result == "pass" else "fail",
                          acceptance=acc, summary="t"),
            "body")

    @staticmethod
    def _run_json(path, steps, overall="fail", exit_rc=None):
        data = {"run_id": "r1", "overall": overall, "steps": steps,
                "skipped": []}
        if exit_rc is not None:
            data["exit_rc"] = exit_rc
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_fail_stage_from_first_bad_step(self):
        rp = self._receipt("fail")
        run = self._run_json(Path(self._tmp.name) / "r.json", [
            {"name": "sync", "rc": 0, "canceled": False},
            {"name": "push", "rc": 1, "canceled": False},
        ])
        s, _ = ws_session.apply_done(self._s, rp, run_state=json.loads(
            Path(run).read_text(encoding="utf-8")))
        self.assertEqual(s["runs"][0]["stage"], "push")
        self.assertEqual(s["runs"][0]["verify_exit"], 1)

    def test_real_rc_replaces_proxy(self):
        # 运行态 exit_rc=3（锁占用）等非 0/1 真实退出码原样入账，
        # 不再被收据 pass/fail 代理 0/1 覆盖
        rp = self._receipt("fail")
        s, _ = ws_session.apply_done(self._s, rp, run_state={
            "overall": "fail", "exit_rc": 3,
            "steps": [{"name": "sync", "rc": 0, "canceled": False}]})
        self.assertEqual(s["runs"][0]["verify_exit"], 3)

    def test_pass_takes_last_executed_step(self):
        rp = self._receipt("pass")
        s, _ = ws_session.apply_done(self._s, rp, run_state={
            "overall": "pass", "exit_rc": 0,
            "steps": [{"name": "sync", "rc": 0, "canceled": False},
                      {"name": "acceptance", "rc": 0, "canceled": False},
                      {"name": "report", "rc": 0, "canceled": False}]})
        self.assertEqual(s["runs"][0]["stage"], "report")
        self.assertEqual(s["runs"][0]["verify_exit"], 0)
        self.assertEqual(s["exit_attribution"], "pass")

    def test_canceled_step_is_stage(self):
        rp = self._receipt("fail")
        s, _ = ws_session.apply_done(self._s, rp, run_state={
            "overall": "fail", "exit_rc": 1,
            "steps": [{"name": "unit_test", "rc": None, "canceled": True}]})
        self.assertEqual(s["runs"][0]["stage"], "unit_test")

    def test_run_state_overrides_stage_arg(self):
        # 同传 --stage 与 --run-file：运行态优先（--stage 仅无运行态时回落）
        rp = self._receipt("fail")
        s, _ = ws_session.apply_done(self._s, rp, stage="acceptance",
                                     run_state={
                                         "overall": "fail", "exit_rc": 1,
                                         "steps": [{"name": "push", "rc": 2,
                                                    "canceled": False}]})
        self.assertEqual(s["runs"][0]["stage"], "push")

    def test_cli_done_run_file(self):
        # CLI 闭环：done --run-file 从文件读运行态并记账
        rp = self._receipt("fail")
        run = self._run_json(Path(self._tmp.name) / "run.json", [
            {"name": "connect", "rc": 0, "canceled": False},
            {"name": "push", "rc": 1, "canceled": False}])
        sp = Path(self._tmp.name) / "session.json"
        ws_session.save_session(self._s, str(sp))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_session.main(["done", "--session", str(sp),
                                  "--receipt", str(rp), "--run-file", str(run)])
        self.assertEqual(rc, 0)
        s = ws_session.load_session(str(sp))
        self.assertEqual(s["runs"][0]["stage"], "push")

    def test_cli_done_bad_run_file_rc1(self):
        # 运行态文件损坏：报错返 1，不记账（不耗轮次）
        rp = self._receipt("fail")
        bad = Path(self._tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        sp = Path(self._tmp.name) / "session.json"
        ws_session.save_session(self._s, str(sp))
        err = io.StringIO()
        from contextlib import redirect_stderr
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = ws_session.main(["done", "--session", str(sp),
                                  "--receipt", str(rp), "--run-file", str(bad)])
        self.assertEqual(rc, 1)
        s = ws_session.load_session(str(sp))
        self.assertEqual(s["total_attempts"], 0)

    def test_stage_rc_fallback_without_exit_rc(self):
        # 产物缺 exit_rc（旧 chain.json）：按 overall 推导 0/1，stage 兼容取末步
        stage, rc = ws_session._stage_rc_from_run_state(
            {"overall": "fail", "steps": [{"name": "sync", "rc": 0}]})
        self.assertEqual((stage, rc), ("sync", 1))


if __name__ == "__main__":
    unittest.main()

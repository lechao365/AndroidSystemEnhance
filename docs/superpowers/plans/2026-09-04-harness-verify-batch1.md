# Harness 验证流水线 Batch-1 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地全量分析报告 Batch-1：收据口径修正（gap 归属 / verify_build missing 根因）、验收段打点税消除、单测二进制幂等推送、trigger 无条件 sleep 2 消除、脚本自报时长基准前移——干净轮 verify 段 159.7s → ~145s，且收据口径完整可归因。

**Architecture:** 全部改动在 `harness/` 侧（不触碰 `code/`/`rpi5` 源码，`source-code-modify.md` 不适用）。核心原则：每项改动先补失败单测，再改实现，跑绿后单独 commit；`verify-cases.yaml` 资产改动配契约断言测试防回退。

**Tech Stack:** Python 3（unittest + mock，与现有 tests 风格一致）、pytest runner（selfcheck 全仓 921 用例）、yaml。

**数据依据（收据 trend）：**
- `gap_before_verify_*` 三段 ~55s/批 被错误归入 edit 相（ws_report.py:85-86）
- 近三批 `missing=["verify_build"]`：`_backfill_zero_marks` 仅 batch-file 模式生效（ws_acceptance.py:779-785 batch_id=None 时 :687-688 直接 return，不回落 `CDP_BATCH_ID`/唯一 timings 文件）
- 验收段每项 1 个 mark 子进程（30+ 项 × 0.1~0.3s 串行叠加，ws_acceptance.py:607-631 subprocess 版），而 `_backfill_zero_marks`（:697）已证明进程内直调 `cdp_timing.main` 可行
- ws_upload_tests.py:172 每 target 无条件 `adb push` 测试二进制（ws_push 幂等回读同构，未复用）
- verify-cases.yaml:103/167 两个 trigger 首项 `adb root ... && sleep 2 && ensure`——已 root 时 adbd 不重启，sleep 2 纯浪费（acc_13/25 恒 2.55s）
- dur_s 自报基准 `_t0` 在 main() 内（sync :914 / push :327 / upload :297），解释器启动+import 成本落 gap；前移到模块级后 gap 收窄为纯 AI 编排活动

---

### Task 1: gap 归属修正（B3）——verify 期 gap 归 verify 相

**Files:**
- Modify: `harness/skills/workspace-verify/ws_report.py:85-87`
- Test: `harness/skills/workspace-verify/tests/test_ws_report.py`

- [ ] **Step 1: 写失败测试**

在 `test_ws_report.py` 的 `TestWsReport` 类内追加（紧邻其他 `_phase_summary` 相关测试，若无则在类末尾追加）：

```python
    def test_phase_summary_verify_gap_counts_to_verify(self):
        # gap_before_verify_* 派生段归 verify 相（此前一律归 edit，verify 前
        # 55s 编排空转被记成编辑，收据 phase 口径失真）
        segs = [{"name": "gap_before_verify_push", "elapsed_s": 13.334},
                {"name": "gap_before_verify_sync", "elapsed_s": 19.886}]
        self.assertEqual(ws_report._phase_summary(segs),
                         {"edit": 0.0, "selfcheck": 0.0, "verify": 33.22,
                          "other": 0.0})

    def test_phase_summary_edit_side_gap_counts_to_edit(self):
        # 编辑侧 gap（apply_selfcheck 前的返工/编辑活动）维持归 edit
        segs = [{"name": "gap_before_apply_selfcheck", "elapsed_s": 481.35},
                {"name": "gap_before_verify_unit_test", "elapsed_s": 21.826}]
        got = ws_report._phase_summary(segs)
        self.assertEqual(got["edit"], 481.35)
        self.assertEqual(got["verify"], 21.826)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_report.py::TestWsReport::test_phase_summary_verify_gap_counts_to_verify -q`
Expected: FAIL（verify=0.0, edit=33.22）

- [ ] **Step 3: 实现**

`ws_report.py:85-87` 原：

```python
        if name.startswith("gap_before_"):
            totals["edit"] += elapsed
            continue
```

改为：

```python
        if name.startswith("gap_before_"):
            # gap 归属按其后继段归类：gap_before_verify_* 的余量是验证环节
            # 的编排活动，归 verify；其余（edit/apply_selfcheck 前）仍归 edit。
            # 一律归 edit 曾把 verify 前 ~55s/批 记成编辑（phase 口径失真）。
            target = _base_seg_name(name[len("gap_before_"):])
            if target.startswith("verify_"):
                totals["verify"] += elapsed
            else:
                totals["edit"] += elapsed
            continue
```

同步更新函数 docstring 中"gap_before_* 派生段一律归入 edit"一句为：
"gap_before_* 派生段按后继段归类（verify 前缀归 verify，其余归 edit）"。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_report.py -q`
Expected: PASS（全文件）

- [ ] **Step 5: Commit**

```bash
git add harness/skills/workspace-verify/ws_report.py harness/skills/workspace-verify/tests/test_ws_report.py
git commit -m "修复(workspace-verify): verify期gap归属verify相修正收据口径"
```

---

### Task 2: verify_build missing 根因修复（B4）——batch_id 三级回落接线

**Files:**
- Modify: `harness/skills/workspace-verify/ws_acceptance.py`（main 内 :777-785 区段）
- Test: `harness/skills/workspace-verify/tests/test_ws_acceptance.py`

- [ ] **Step 1: 写失败测试**

在 `test_ws_acceptance.py` 的 `TestBackfillZeroMarks` 类后新增测试类：

```python
class TestResolveRunBatchId(unittest.TestCase):
    """main 内 batch_id 解析三级回落（显式 batch-file > CDP_BATCH_ID >
    唯一 timings 文件）：--case 模式未解析出 batch_id 时回落识别，否则
    _backfill_zero_marks 直接 return，verify_build 等标准段永远 missing
    （0904 三批 missing=[verify_build] 的根因）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self.batch = "abc123def456"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old
        self._tmp.cleanup()

    def test_batch_file_text_wins(self):
        # batch-file 可解析 → 显式值优先（batch_id_from_text 的解析正确性
        # 属 cdp_parse 既有职责，此处 mock 隔离，只验证回落优先级）
        with mock.patch.object(wa, "batch_id_from_text",
                               return_value=self.batch):
            self.assertEqual(wa._resolve_run_batch_id("/tmp/b.txt"),
                             self.batch)

    def test_batch_file_unreadable_falls_back_to_env(self):
        # batch-file 读取失败 → 回落 CDP_BATCH_ID（与 _mark_stage 同口径）
        with mock.patch.dict("os.environ", {"CDP_BATCH_ID": self.batch}):
            self.assertEqual(wa._resolve_run_batch_id("/no/such/file.txt"),
                             self.batch)

    def test_no_file_no_env_uses_unique_timings_file(self):
        # 无 batch-file 无 env → log 目录唯一 timings 文件 stem
        wa.cdp_timing.main(["start", "--batch", self.batch])
        self.assertEqual(wa._resolve_run_batch_id(None), self.batch)

    def test_nothing_resolvable_returns_none(self):
        # 三级皆缺 → None（调用方补零/mark 静默跳过，防误标其他批次）
        self.assertIsNone(wa._resolve_run_batch_id(None))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_acceptance.py::TestResolveRunBatchId -q`
Expected: FAIL（`AttributeError: module 'ws_acceptance' has no attribute '_resolve_run_batch_id'`）

- [ ] **Step 3: 实现**

`ws_acceptance.py` 在 `_backfill_zero_marks` 定义之后（:699 附近）新增：

```python
def _resolve_run_batch_id(batch_file):
    """main 内 batch_id 解析三级回落：batch-file 显式解析 > CDP_BATCH_ID >
    log 目录唯一 timings 文件（复用 _resolve_batch_id 口径）。

    --case/--acceptance 模式此前 batch_id 恒 None，_backfill_zero_marks
    直接 return——标准段跳过时 verify_build 永远 missing（0904 三批实证）。
    回落识别后补零/mark 必落本批打点文件；多打点文件时 _resolve_batch_id
    静默跳过，防误标其他批次。读取失败按未提供处理（回落继续）。
    """
    if batch_file:
        try:
            bid = batch_id_from_text(
                Path(batch_file).read_text(encoding="utf-8"))
            if bid:
                return bid
        except (OSError, UnicodeDecodeError):
            pass
    return _resolve_batch_id(None)
```

`main` 内 :777-785 原：

```python
    # 模式 A（batch-file）显式解析 batch_id：内部分段/case 级/补零 mark
    # 全部落本批打点文件（多打点文件时自动识别静默跳过，缺段无法归因）
    batch_id = None
    if args.batch_file:
        try:
            batch_id = batch_id_from_text(
                Path(args.batch_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            batch_id = None
```

改为：

```python
    # batch_id 解析三级回落（batch-file > CDP_BATCH_ID > 唯一 timings 文件）：
    # --case 模式此前恒 None 致 _backfill_zero_marks 直接 return，标准段
    # 跳过时 verify_build 永远 missing（0904 三批实证）；回落识别与
    # _mark_stage 同口径，多打点文件时静默跳过防误标其他批次
    batch_id = _resolve_run_batch_id(args.batch_file)
```

同步更新 `_backfill_zero_marks` docstring 中 "batch_id 缺失（非 batch-file 模式）时跳过——自动识别不可靠时不写，防误标其他批次" 为：
"batch_id 缺失（三级回落皆不可得）时跳过——自动识别不可靠时不写，防误标其他批次"。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_acceptance.py -q`
Expected: PASS（全文件，含既有 TestBackfillZeroMarks.test_no_batch_id_skips——函数级语义未变）

- [ ] **Step 5: Commit**

```bash
git add harness/skills/workspace-verify/ws_acceptance.py harness/skills/workspace-verify/tests/test_ws_acceptance.py
git commit -m "修复(workspace-verify): batch_id三级回落接线修verify_build缺段根因"
```

---

### Task 3: 验收段打点税消除——ws_acceptance._mark_stage 进程内直调

**Files:**
- Modify: `harness/skills/workspace-verify/ws_acceptance.py:607-631`（`_mark_stage`）
- Test: `harness/skills/workspace-verify/tests/test_ws_acceptance.py`

- [ ] **Step 1: 确认现有测试无 subprocess mock 依赖**

Run: `grep -n "_mark_stage\|subprocess" harness/skills/workspace-verify/tests/test_ws_acceptance.py`
Expected: 若存在对 `_mark_stage` 走 subprocess 的 mock 用例则一并改造；无则继续。

- [ ] **Step 2: 写失败测试**

在 `test_ws_acceptance.py` 追加（TestBackfillZeroMarks 类后）：

```python
class TestMarkStageInProcess(unittest.TestCase):
    """验收段每项一发 mark（30+ 次）：_mark_stage 改进程内直调
    cdp_timing.main（_backfill_zero_marks 同款先例），消除逐项子进程
    启动开销（0.1~0.3s × 30+ 在验收段内串行叠加）。"""

    def test_calls_cdp_timing_main_in_process(self):
        with mock.patch.object(wa.cdp_timing, "main",
                               return_value=0) as m:
            wa._mark_stage("verify_acceptance_acc_3", "batch001")
        m.assert_called_once()
        args = m.call_args.args[0]
        self.assertEqual(args, ["mark", "--name", "verify_acceptance_acc_3",
                                "--batch", "batch001"])

    def test_zero_flag_passthrough(self):
        with mock.patch.object(wa.cdp_timing, "main", return_value=0) as m:
            wa._mark_stage("verify_sync", "batch001", zero=True)
        self.assertIn("--zero", m.call_args.args[0])

    def test_nonzero_rc_warns_not_raises(self):
        # cdp_timing 返回非 0 → 仅 warn 不阻断（失败不阻断口径语义不变）
        with mock.patch.object(wa.cdp_timing, "main", return_value=3):
            wa._mark_stage("verify_acceptance", None)

    def test_exception_warns_not_raises(self):
        with mock.patch.object(wa.cdp_timing, "main",
                               side_effect=RuntimeError("boom")):
            wa._mark_stage("verify_acceptance", None)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_acceptance.py::TestMarkStageInProcess -q`
Expected: FAIL（现走 subprocess，cdp_timing.main 未被调用）

- [ ] **Step 4: 实现**

`ws_acceptance.py:607-631` 原 `_mark_stage` 整体替换为：

```python
def _mark_stage(name, batch_id=None, zero=False):
    """验证阶段自动打点：进程内直调 cdp_timing.main mark（batch 识别：显式
    batch_id > 环境变量 CDP_BATCH_ID > log 目录唯一 timings 文件；均缺时
    静默跳过返 0，失败不阻断口径）。验收段每项一发 mark（30+ 次），子进程
    版每次 0.1~0.3s 启动开销串行叠加，进程内直调消除（_backfill_zero_marks
    同款先例）。zero=True 记零 mark（跳过段占位，段耗时 0）。"""
    args = ["mark", "--name", name]
    if batch_id:
        args += ["--batch", batch_id]
    if zero:
        args += ["--zero"]
    try:
        rc = cdp_timing.main(args)
    except SystemExit as e:
        rc = e.code
    except Exception as e:
        print(f"warn: 打点 {name} 失败（不阻断）: {e}", file=sys.stderr)
        return
    if rc not in (0, None):
        print(f"warn: 打点 {name} rc={rc}（不阻断）", file=sys.stderr)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_acceptance.py -q`
Expected: PASS（全文件）

- [ ] **Step 6: Commit**

```bash
git add harness/skills/workspace-verify/ws_acceptance.py harness/skills/workspace-verify/tests/test_ws_acceptance.py
git commit -m "重构(workspace-verify): 验收段mark改进程内直调消除打点税"
```

---

### Task 4: 单测二进制幂等推送（A5）

**Files:**
- Modify: `harness/skills/workspace-verify/ws_upload_tests.py`（`_run_one_stats` :162-180 区段 + 新 helper + import hashlib）
- Test: `harness/skills/workspace-verify/tests/test_ws_upload_tests.py`

- [ ] **Step 1: 写失败测试**

`test_ws_upload_tests.py` 顶部 import 区追加 `import hashlib`（若无）。
`TestRunOne` 类后新增：

```python
class TestDeviceBinaryFingerprint(unittest.TestCase):
    """幂等推送比对基准：设备侧 sha256+bytes 回读，任一缺失返回 None
    （回读不可信不得跳过推送，对齐 ws_push.readback_device 口径）。"""

    def test_reads_sha_and_bytes(self):
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("abc123  /data/local/tmp/t1", 0),
            ("4096", 0),
        ]) as m:
            fp = wu.device_binary_fingerprint("ep", "t1")
        self.assertEqual(fp, {"sha256": "abc123", "bytes": 4096})
        self.assertEqual(m.call_args_list[0].args[1],
                         ["shell", "sha256sum /data/local/tmp/t1"])
        self.assertEqual(m.call_args_list[1].args[1],
                         ["shell", "stat -c %s /data/local/tmp/t1"])

    def test_missing_sha_returns_none(self):
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # sha256sum 失败
            ("4096", 0),
        ]):
            self.assertIsNone(wu.device_binary_fingerprint("ep", "t1"))

    def test_missing_bytes_returns_none(self):
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("abc123  /data/local/tmp/t1", 0),
            ("", 1),  # stat 失败
        ]):
            self.assertIsNone(wu.device_binary_fingerprint("ep", "t1"))


class TestIdempotentPush(unittest.TestCase):
    """方向 A5：推送前回读设备侧指纹，与本地全等跳过 adb push（二进制
    未变不重推）；sha 等字节不等不跳；回读缺失照推。"""

    GTEST_OK = ("[==========] 42 tests from 2 test suites ran. "
                "(100 ms total)\n")

    def _mk_binary(self, content=b"x"):
        d = tempfile.mkdtemp()
        p = (Path(d) / "out" / "target" / "product" / "rpi5"
             / "data" / "nativetest64" / "t1" / "t1")
        p.parent.mkdir(parents=True)
        p.write_bytes(content)
        return str(p.parent.parent.parent.parent.parent), d

    def test_identical_skips_push(self):
        content = b"same-bytes"
        out, d = self._mk_binary(content)
        sha = hashlib.sha256(content).hexdigest()
        with mock.patch.object(wu, "adb_run", side_effect=[
            (f"{sha}  /data/local/tmp/t1", 0),  # readback sha
            (str(len(content)), 0),             # readback bytes
            ("", 0),                            # chmod
            (self.GTEST_OK, 0),                 # gtest
        ]) as m:
            ok, detail, stats = wu.run_one("ep", out, "rpi5", "t1",
                                           return_stats=True)
        cmds = [c.args[1][0] for c in m.call_args_list]
        self.assertNotIn("push", cmds)
        self.assertTrue(ok)
        self.assertIn("PASS", detail)

    def test_same_sha_diff_bytes_still_pushes(self):
        content = b"same-bytes"
        out, d = self._mk_binary(content)
        sha = hashlib.sha256(content).hexdigest()
        with mock.patch.object(wu, "adb_run", side_effect=[
            (f"{sha}  /data/local/tmp/t1", 0),  # readback sha 全等
            ("1", 0),                           # bytes 不等
            ("", 0),                            # push
            ("", 0),                            # chmod
            (self.GTEST_OK, 0),                 # gtest
        ]) as m:
            ok, detail, stats = wu.run_one("ep", out, "rpi5", "t1",
                                           return_stats=True)
        cmds = [c.args[1][0] for c in m.call_args_list]
        self.assertIn("push", cmds)
        self.assertEqual(stats["pushed"], True)

    def test_readback_missing_still_pushes(self):
        out, d = self._mk_binary()
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),        # readback sha 失败
            ("", 1),        # readback bytes 失败
            ("", 0),        # push
            ("", 0),        # chmod
            (self.GTEST_OK, 0),
        ]) as m:
            ok, detail, stats = wu.run_one("ep", out, "rpi5", "t1",
                                           return_stats=True)
        cmds = [c.args[1][0] for c in m.call_args_list]
        self.assertIn("push", cmds)
        self.assertTrue(ok)
```

同步更新既有 `TestRunOne` 中走 push 路径的 5 个用例（`test_pass_returns_ok` / `test_pass_stats_failed_zero` / `test_fail_returns_not_ok_with_output` / `test_zero_cases_rejected` / `test_missing_summary_rejected`）：每个 `side_effect` 列表头部插入两项回读响应（回读失败 → 照推，保持原判定语义）：

```python
        with mock.patch.object(wu, "adb_run", side_effect=[
            ("", 1),  # readback sha 失败（幂等跳过不生效）
            ("", 1),  # readback bytes 失败
            ("", 0),  # push
            ("", 0),  # chmod
            ("<原 gtest 输出>", <原 rc>),
        ]):
```

`test_push_failure_returns_not_ok` 用 `return_value=("", 1)` 全调用失败：readback 返回 `("", 1)` → device=None → 照推 → push rc=1，判定语义不变，无需改动。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_upload_tests.py -q`
Expected: FAIL（`AttributeError: ... has no attribute 'device_binary_fingerprint'`）

- [ ] **Step 3: 实现**

`ws_upload_tests.py` import 区追加 `import hashlib`。
`_run_one_stats` 前（:156 前）新增：

```python
def device_binary_fingerprint(ep, name):
    """回读设备侧测试二进制指纹：SHA256 + 字节数（幂等推送比对基准）。

    两项独立 exec（任一失败不掩盖其余）；sha256/bytes 任一缺失返回 None
    （回读不可信不得跳过推送，对齐 ws_push.readback_device 口径）。
    """
    out, rc = adb_run(ep, ["shell", f"sha256sum /data/local/tmp/{name}"],
                      timeout=60)
    sha = None
    if rc == 0:
        parts = out.strip().split()
        if parts and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            sha = parts[0]
    out, rc = adb_run(ep, ["shell", f"stat -c %s /data/local/tmp/{name}"],
                      timeout=60)
    nbytes = None
    if rc == 0 and out.strip().isdigit():
        nbytes = int(out.strip())
    if not sha or nbytes is None:
        return None
    return {"sha256": sha, "bytes": nbytes}
```

`_run_one_stats` 中 :172-175 原：

```python
    _, rc = adb_run(ep, ["push", binary, f"/data/local/tmp/{name}"], timeout=300)
    if rc != 0:
        return False, f"{name}: adb push 失败 rc={rc}", {
            "name": name, "rc": 1, "tests": None, "failed": None}
```

改为：

```python
    # 幂等推送（方向 A5）：推送前回读设备侧 SHA256+字节，与本地全等则跳过
    # push（二进制未变不重推，对齐 ws_push 幂等口径）；回读缺失（任一
    # 不可得）按原路推——回读不可信不得跳过。pushed 入 stats 供归因。
    local_bytes = Path(binary).read_bytes()
    local_sha = hashlib.sha256(local_bytes).hexdigest()
    device = device_binary_fingerprint(ep, name)
    pushed = not (device is not None
                  and device["sha256"] == local_sha
                  and device["bytes"] == len(local_bytes))
    if pushed:
        _, rc = adb_run(ep, ["push", binary, f"/data/local/tmp/{name}"],
                        timeout=300)
        if rc != 0:
            return False, f"{name}: adb push 失败 rc={rc}", {
                "name": name, "rc": 1, "tests": None, "failed": None,
                "pushed": True}
```

紧随其后的 chmod/exec 两个失败分支 stats 追加 `"pushed": pushed`；成功/失败最终返回的三个 stats dict（:212-214 / :217-218 / 早期返回 :166-171 与 :169-171）同样补 `"pushed": pushed`（:166-171 早期返回在 pushed 计算前，保持不含 pushed 键即可——产物缺失/陈旧未到推送环节）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_upload_tests.py -q`
Expected: PASS（全文件，含更新后的 5 个既有用例）

- [ ] **Step 5: Commit**

```bash
git add harness/skills/workspace-verify/ws_upload_tests.py harness/skills/workspace-verify/tests/test_ws_upload_tests.py
git commit -m "新增(workspace-verify): 单测二进制幂等推送回读全等跳过"
```

---

### Task 5: trigger 首项条件等待（A4，verify-cases.yaml）

**Files:**
- Modify: `harness/config/verify-cases.yaml:103`（lcview-trigger）、`:167`（lciod-trigger）
- Test: `harness/skills/workspace-verify/tests/test_verify_cases_yaml.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `harness/skills/workspace-verify/tests/test_verify_cases_yaml.py`：

```python
# verify-cases.yaml 资产契约断言：trigger 首项 root 等待须条件化
# （adb root 输出含 already 即 adbd 未重启，无条件 sleep 2 纯浪费——
# acc_13/25 恒 2.55s 的构成，A4 消除）。断言锚点用 "already"/"grep -q"
# 子串（无条件版两者皆无），规避 payload 内转义引号的切分歧义。

import unittest
from pathlib import Path

import yaml

_YAML = (Path(__file__).resolve().parents[3] / "config"
         / "verify-cases.yaml")


class TestTriggerFirstRootWait(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))

    def _acceptance(self, case):
        text = self.data["cases"][case]["acceptance"]
        self.assertTrue(text.startswith("hostcmd:"),
                        f"{case} 首项应为 hostcmd")
        return text

    def test_lcview_trigger_conditional_wait(self):
        text = self._acceptance("lcview-trigger")
        self.assertIn("grep -q", text)
        self.assertIn("already", text)  # 无条件版两者皆无（回归锚点）
        self.assertIn("sleep 2", text)  # 条件分支内保留（真实切换仍须等待）

    def test_lciod_trigger_conditional_wait(self):
        text = self._acceptance("lciod-trigger")
        self.assertIn("grep -q", text)
        self.assertIn("already", text)

    def test_yaml_loads_and_cases_intact(self):
        self.assertIn("lcview-liveness", self.data["cases"])
        self.assertIn("lciod-liveness", self.data["cases"])
        self.assertTrue(self.data.get("modules"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_verify_cases_yaml.py -q`
Expected: FAIL（现命令无 `grep -q "already"`）

- [ ] **Step 3: 实现**

`verify-cases.yaml` :103 lcview-trigger acceptance 首项：

```
hostcmd:"python3 ws_adb_connect.py ensure >/dev/null && adb root >/dev/null 2>&1 && sleep 2 && python3 ws_adb_connect.py ensure >/dev/null"
```

改为：

```
hostcmd:"python3 ws_adb_connect.py ensure >/dev/null && { adb root 2>&1 | grep -q \"already\" || { sleep 2 && python3 ws_adb_connect.py ensure >/dev/null; }; }"
```

`:167` lciod-trigger 首项同款替换。

语义：`adb root` 已 root（输出 `adbd is already running as root`）→ grep 命中 → 跳过 sleep 与二次 ensure（省 ~2s）；真实切换（`restarting adbd as root`）或失败 → sleep 2 + re-ensure（原行为）。后续 sysfs 写项依赖 root 的语义不变。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_verify_cases_yaml.py -q && python3 -c "import yaml,sys; d=yaml.safe_load(open('harness/config/verify-cases.yaml')); print('yaml ok', len(d['cases']))"`
Expected: PASS + `yaml ok 11`

- [ ] **Step 5: Commit**

```bash
git add harness/config/verify-cases.yaml harness/skills/workspace-verify/tests/test_verify_cases_yaml.py
git commit -m "重构(harness): trigger首项root等待条件化去无条件sleep2"
```

---

### Task 6: dur_s 自报基准前移（A1）——gap 收窄为纯编排活动

**Files:**
- Modify: `harness/skills/workspace-verify/ws_push.py`（:58 后、:327）
- Modify: `harness/skills/workspace-verify/ws_upload_tests.py`（:35 后、:297）
- Modify: `harness/skills/sync-code-to-workspace/sync_code_to_workspace.py`（模块级、:914）

**说明：** 本 task 无新单测——行为等价（仅时长基准取值来源变化），既有 `TestMarkStageDurS`（三处）回归覆盖传参语义；模块级常量不可直接单测。

- [ ] **Step 1: ws_push.py**

`_sleep = time.sleep`（:58）之后追加：

```python

# dur_s 自报基准（方向 1）：模块级取值——解释器启动后的 import 成本一并
# 归入脚本自报时长，gap_before_verify_push 收窄为纯 AI 编排活动（编排与
# 脚本成本的边界按进程边界切分）
_T0 = time.monotonic()
```

main 内 :327 `_t0 = time.monotonic()` 改为 `_t0 = _T0`（注释 "方向 1：脚本自报实测时长基准" 保留）。

- [ ] **Step 2: ws_upload_tests.py**

模块 import 区结束（`_CASES_PATH = ...` :35）之后追加：

```python

# dur_s 自报基准（方向 1）：模块级取值——import 成本归入脚本自报时长，
# gap 收窄为纯 AI 编排活动（边界按进程边界切分）
_T0 = time.monotonic()
```

main 内 :297 `_t0 = time.monotonic()` 改为 `_t0 = _T0`。

- [ ] **Step 3: sync_code_to_workspace.py**

`_mark_stage` 定义前（模块级，:886 前）追加：

```python
# dur_s 自报基准（方向 1）：模块级取值——import 成本归入脚本自报时长，
# gap_before_verify_sync 收窄为纯 AI 编排活动（边界按进程边界切分）
_T0 = time.monotonic()
```

main 内 :914 `_t0 = time.monotonic()` 改为 `_t0 = _T0`。

- [ ] **Step 4: 回归验证**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_push.py harness/skills/workspace-verify/tests/test_ws_upload_tests.py harness/skills/sync-code-to-workspace/tests/test_sync_code_to_workspace.py -q`
Expected: PASS（全绿）

- [ ] **Step 5: Commit**

```bash
git add harness/skills/workspace-verify/ws_push.py harness/skills/workspace-verify/ws_upload_tests.py harness/skills/sync-code-to-workspace/sync_code_to_workspace.py
git commit -m "重构(harness): dur_s基准前移模块级gap收窄为纯编排活动"
```

---

### Task 7: 全量自检收口

- [ ] **Step 1: 全仓自检（对齐收据 selfcheck 口径）**

Run: `python3 harness/lib/selfcheck.py`
Expected: rc=0，pytest 全过（≥930），refs/config/contract 全 0

- [ ] **Step 2: 上板验证（用户执行）**

本计划为 harness 侧改动，按 AGENTS.md 测试防护条款不涉及 lcview/lciod 源码，但收据口径改动（Task 1/2/3）需上板闭环验证一次：走 `/workspace-verify` 生成新收据，核对：
- `missing` 不再含 `verify_build`（Task 2）
- `phase_summary` 中 verify 期 gap 归 verify（Task 1）
- 验收段总耗时下降（Task 3/4/5）
- trigger 首项（acc_13/acc_25）~2.5s → ~0.5s（Task 5）

---

## 后续批次（不在本计划实施，仅登记依赖关系）

| 批次 | 内容 | 前置 |
|---|---|---|
| Batch-2 度量基建 | B1 根治 KI-20260902-001（edit 收口制度化）、B2 edit 段按变更点细分打点、B5 report/finish 盲区收编（content_tree pathspec 收窄 + prune_details 缓存）、B6 用例两级策略契约化、A2 失败轮重连快速失败预算、A6 sync 落盘校验增量复用 | 本批收据数据 |
| Batch-3 主攻 | edit 侧（emit 批参数权衡 / loop 修复编辑入账 / 批次拆分策略） | B2 细分数据 |

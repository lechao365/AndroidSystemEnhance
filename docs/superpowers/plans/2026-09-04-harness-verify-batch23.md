# Harness 验证流水线 Batch-2/3（度量基建 + loop 入账）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 Batch-2 度量基建（B1 edit 收口制度化 / B2 edit_item 分方向打点 / B5 report_post 盲区收编 / B6 用例两级策略 / A2 重连快速失败预算 / verify chain 编排串联）与 Batch-3（C3 loop 修复编辑入账），并按 KIR-006 闭环 KI-20260902-001。

**Architecture:** 全部改动在 `harness/` 与 `data/known-issues/`（不触碰 `code/`）。核心思路：把"编辑区间"的打点从 AI 自判改为脚本自动收口（selfcheck 开跑前补打），把收据盲区（report 尾部工作）纳入段表，把编排层 AI 往返（sync→push→unit_test 三步）收敛为单脚本串联。

**方案确认结论（本计划剔除项及理由）：**
- **A6 sync 落盘校验增量复用——剔除**：NEW-DIFF 检测要求 apply 后全量重扫（检测新偏离是安全语义），~12s 是必要成本，无语义安全的增量方案。
- **C2 emit 批参数权衡——剔除**：单点 80~141s 的内部分布需 B2 落地后积累 3-5 批收据数据才能盲调变明调，现在动参数违反数据驱动原则。
- **批次拆分策略——剔除**：同上，待 B2 数据。

**Tech Stack:** Python 3（unittest + mock）、pytest、bash、yaml。

---

### Task 1: B1 edit 收口制度化（selfcheck 自动补打，根治 KI-20260902-001）

**Files:**
- Modify: `harness/lib/selfcheck.py`（新增 `_ensure_edit_close_mark` + main() 调用）
- Test: Create `harness/lib/tests/test_selfcheck_edit_close.py`

- [ ] **Step 1: 写失败测试**（新建 `harness/lib/tests/test_selfcheck_edit_close.py`）

```python
# selfcheck 编辑收口自动补打（B1，根治 KI-20260902-001）：mark edit 此前由
# apply AI 自判"编辑完成"——-s 批实测漂移（edit mark 打在两轮自检之后，
# 501.9s+315.6s 真实编辑散落 gap）。制度化：selfcheck 开跑前若编辑未收口
# 则自动补打 mark edit（同名自动 #N），编辑区间口径不再依赖 AI 自判。

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# cdp_timing 与 selfcheck 同仓内部复用（打点文件构造/读取断言用）
_CDP_LIB = (Path(__file__).resolve().parents[3] / "harness" / "skills"
            / "cross-device" / "lib" / "python")
sys.path.insert(0, str(_CDP_LIB))

import selfcheck as sc


class TestEnsureEditCloseMark(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old
        self._tmp.cleanup()

    def _mk_timing(self, marks):
        """构造活跃批打点文件并指向 current-batch.json，返回 batch_id。"""
        import json

        import cdp_timing
        bid = "abc123def456"
        cdp_timing.main(["start", "--batch", bid])
        p = cdp_timing._timing_path(bid)
        data = json.loads(p.read_text(encoding="utf-8"))
        data["marks"] = marks
        p.write_text(json.dumps(data, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        return bid

    def test_no_edit_mark_backfills(self):
        # 无 edit mark（AI 漏打）→ selfcheck 开跑前自动补打 edit
        bid = self._mk_timing([{"name": "edit_plan", "wall": 100.0}])
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        self.assertEqual(m.call_args.args[0][3:7],
                         ["mark", "--batch", bid, "--name"])
        self.assertEqual(m.call_args.args[0][7], "edit")

    def test_edit_before_selfcheck_no_backfill(self):
        # 已有 edit 且在自检之前（正常 AI 手打路径）→ 不重复补打
        self._mk_timing([{"name": "edit", "wall": 100.0}])
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        m.assert_not_called()

    def test_edit_stale_after_selfcheck_backfills(self):
        # -s 批漂移形态：edit 打在 apply_selfcheck 之后（真实编辑未收口）
        # → 补打 edit（同名自动 #2），把后续真实编辑区间重新归口
        bid = self._mk_timing([
            {"name": "edit", "wall": 100.0},
            {"name": "apply_selfcheck", "wall": 200.0},
        ])
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        self.assertEqual(m.call_args.args[0][7], "edit")
        self.assertIn("--batch", m.call_args.args[0])
        self.assertIn(bid, m.call_args.args[0])

    def test_no_active_batch_skips_silently(self):
        # 无活跃批（emit 侧独立自测等）→ 静默跳过不报错
        with mock.patch.object(sc.subprocess, "run") as m:
            sc._ensure_edit_close_mark()
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/lib/tests/test_selfcheck_edit_close.py -q`
Expected: FAIL（`AttributeError: module 'selfcheck' has no attribute '_ensure_edit_close_mark'`）

- [ ] **Step 3: 实现**

`harness/lib/selfcheck.py` 在 `_mark_selfcheck` 定义之后新增：

```python
def _ensure_edit_close_mark():
    """编辑收口自动补打（B1，根治 KI-20260902-001）。

    mark edit 此前由 apply AI 自判"编辑完成"——-s 批实测漂移（edit mark
    打在两轮自检之后，真实编辑散落 gap_before_apply_selfcheck），edit 段
    口径跨批不可比。制度化：本函数在自检起跑前判定编辑是否已收口，未收口
    则补打 mark edit（cdp_timing 同名 mark 自动 #N 序号）：
      - marks 无 edit → 补打（AI 漏打）
      - 末个 edit 在末个 apply_selfcheck 之后 → 补打（loop 轮修复编辑 /
        -s 批漂移形态，把后续真实编辑重新归口为 edit#N）
      - 其余（edit 已收口）→ 不动
    batch 定位复用 cdp_timing 三级回落（CDP_BATCH_ID > current-batch.json
    指针）；无活跃批（emit 侧独立自测）静默跳过。补打失败仅 warn 不阻断
    （打点诊断数据，非自检结果本身）。
    """
    timing_dir = ROOT / "harness" / "skills" / "cross-device" / "lib" / "python"
    if str(timing_dir) not in sys.path:
        sys.path.insert(0, str(timing_dir))
    try:
        import cdp_timing
        bid = os.environ.get("CDP_BATCH_ID", "").strip() or \
            cdp_timing._read_current_batch()
        if not bid:
            return
        data = cdp_timing._load(cdp_timing._timing_path(bid))
        marks = (data or {}).get("marks") or []
        edit_idx = [i for i, m in enumerate(marks) if m.get("name") == "edit"]
        selfcheck_idx = [i for i, m in enumerate(marks)
                         if m.get("name") == "apply_selfcheck"]
        # 已收口：已有 edit 且（无自检 或 末个 edit 在末个 apply_selfcheck
        # 之后——正常 AI 手打路径）→ 不补
        if edit_idx and (not selfcheck_idx
                         or edit_idx[-1] > selfcheck_idx[-1]):
            return
        # 未收口：无 edit（AI 漏打）或末个 edit 早于末个自检（loop 轮修复
        # 编辑 / -s 批漂移形态）→ 补打，把后续真实编辑重新归口为 edit#N
        timing = timing_dir / "cdp_timing.py"
        cmd = [sys.executable, str(timing), "mark", "--batch", bid,
               "--name", "edit"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  cwd=ROOT, timeout=10)
            if proc.returncode != 0:
                print(f"warn: edit 收口补打失败（不阻断）: "
                      f"{proc.stderr.strip()}", file=sys.stderr)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"warn: edit 收口补打失败（不阻断）: {e}", file=sys.stderr)
    except Exception as e:  # 打点诊断数据，任何异常不得阻断自检
        print(f"warn: edit 收口判定失败（不阻断）: {e}", file=sys.stderr)
```

`main()` 在 `_t0 = time.time()`（:116）之后、`pytest_cmd` 构造之前插入一行：

```python
    _ensure_edit_close_mark()
```

并在文件顶部 import 区补 `import os`（现无）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/lib/tests/test_selfcheck_edit_close.py -q`
Expected: PASS（4 用例）

- [ ] **Step 5: Commit**

```bash
git add harness/lib/selfcheck.py harness/lib/tests/test_selfcheck_edit_close.py
git commit -m "新增(harness): selfcheck自动edit收口根治编辑打点AI自判漂移"
```

---

### Task 2: B2 edit_item 分方向细分打点

**Files:**
- Modify: `harness/skills/cross-device/lib/python/cdp_timing.py:46-59`（KNOWN_SEGMENTS / CONDITIONAL_SEGMENTS）
- Modify: `harness/skills/workspace-verify/ws_report.py:63-65`（_EDIT_SEGMENTS）
- Modify: `harness/skills/cross-device/cross-device-apply/SKILL.md`（:63-64 区段打点指引）
- Test: `harness/skills/cross-device/tests/test_cdp_timing.py`、`harness/skills/workspace-verify/tests/test_ws_report.py`

- [ ] **Step 1: 写失败测试**

`test_cdp_timing.py` 追加（沿用文件既有测试风格）：

```python
    def test_edit_item_in_known_and_conditional(self):
        # B2：分方向编辑打点段（同名自动 #N 序号）；条件段（未打不判缺）
        self.assertIn("edit_item", cdp_timing.KNOWN_SEGMENTS)
        self.assertIn("edit_item", cdp_timing.CONDITIONAL_SEGMENTS)

    def test_edit_item_repeat_marks_get_suffix_segments(self):
        # 同名 edit_item 重复 mark → edit_item / edit_item#2 / edit_item#3
        cdp_timing.main(["start", "--batch", self.batch])
        for _ in range(3):
            cdp_timing.main(["mark", "--batch", self.batch, "--name",
                             "edit_item"])
        data = json.loads(self._timing().read_text(encoding="utf-8"))
        names = [s["name"] for s in cdp_timing.compute_segments(data)]
        self.assertEqual([n for n in names if n.startswith("edit_item")],
                         ["edit_item", "edit_item#2", "edit_item#3"])
```

（`self.batch`/`_timing` helper 若与该文件既有 setUp 不一致，以文件现状为准适配。）

`test_ws_report.py` 的 `TestPhaseSummary` 内追加：

```python
    def test_phase_summary_edit_item_counts_to_edit(self):
        # B2：edit_item 分方向段归 edit 相（同名 #N 剥序号后归类）
        segs = [{"name": "edit_item", "elapsed_s": 120.5},
                {"name": "edit_item#2", "elapsed_s": 80.3}]
        got = ws_report._phase_summary(segs)
        self.assertEqual(got["edit"], 200.8)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/cross-device/tests/test_cdp_timing.py harness/skills/workspace-verify/tests/test_ws_report.py -q`
Expected: 新增 3 用例 FAIL

- [ ] **Step 3: 实现**

1. `cdp_timing.py` KNOWN_SEGMENTS（:46-51）追加 `"edit_item"`；CONDITIONAL_SEGMENTS（:57-59）追加 `"edit_item"`；同步更新两表上方注释（edit_item：分方向编辑打点，apply 每完成一个方向 mark 一次，同名自动 #N，单方向耗时逐项可见）。
2. `ws_report.py` `_EDIT_SEGMENTS`（:63-65）追加 `"edit_item"`。
3. `cross-device-apply/SKILL.md` :63-64 区段（"编辑自愈重试前打点…编辑完成打点…"）之间插入一行指引：

```markdown
每个方向编辑完成后打点（必做）：cdp_timing.py mark --batch <batch_id> --name edit_item
   （同名重复 mark 自动 #N 序号，分方向耗时在收据 segments 逐项可见；
    全部方向完成后再打 edit 收口）
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/cross-device/tests/test_cdp_timing.py harness/skills/workspace-verify/tests/test_ws_report.py -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add harness/skills/cross-device/lib/python/cdp_timing.py harness/skills/workspace-verify/ws_report.py harness/skills/cross-device/cross-device-apply/SKILL.md harness/skills/cross-device/tests/test_cdp_timing.py harness/skills/workspace-verify/tests/test_ws_report.py
git commit -m "新增(harness): edit_item分方向编辑打点段表与指引接线"
```

---

### Task 3: B5 report_post 盲区收编（含 _resolve_timings 后移）

**Files:**
- Modify: `harness/skills/cross-device/lib/python/cdp_timing.py`（KNOWN_SEGMENTS 追加 `"report_post"`，非条件段）
- Modify: `harness/skills/workspace-verify/ws_report.py`（`_mark_report` 重构出共享直写 helper；content_tree 前打 report_post；`_resolve_timings` 调用后移到尾部工作完成后）
- Test: `harness/skills/cross-device/tests/test_cdp_timing.py`、`harness/skills/workspace-verify/tests/test_ws_report.py`

**背景**：report mark（:586）之后才执行 content_tree（全树 `git add -A`，drvfs IO 放大 10-50 倍）、git status、Receipt 构造、write_receipt（prune_details 读全目录收据）、append_trend——这些真实开销不落任何段（`_resolve_timings` 在 :591 算 segments 时 finish 定格，其后追加的 mark 不进收据）。收编方案：content_tree 完成后直写 `report_post` mark + `_resolve_timings` 后移。**段归因方向是"前一 mark → 本 mark"（mark 打在阶段末）**，故 report_post 必须打在尾部工作完成之后：report_post 段 = report mark → report_post mark（覆盖门禁+尾部开销），finish = report_post → 算段时刻 ≈ 0。（执行修正记录：初版把 mark 放 content_tree 之前，审查实证段语义颠倒，已后移修复，见 commit f14a988。）

- [ ] **Step 1: 写失败测试**

`test_cdp_timing.py` 追加：

```python
    def test_report_post_in_known_segments(self):
        # B5：report 尾部工作（content_tree/收据落盘/trend）段，非条件段
        self.assertIn("report_post", cdp_timing.KNOWN_SEGMENTS)
        self.assertNotIn("report_post", cdp_timing.CONDITIONAL_SEGMENTS)
```

`test_ws_report.py` 追加（TestWsReport 内，沿用既有 timings 产物测试模式）：

```python
    def test_mode_a_report_post_mark_recorded(self):
        # B5：ws_report 执行后在打点文件留 report_post mark（尾部工作可归因）
        # —— 断言 timings-<batch>.json 的 marks 含 report_post（在 report 之后）
```

（具体断言仿照既有 `test_mode_a_timings_file_written`：跑一次 ws_report main 落收据后读打点文件，`names.index("report_post") > names.index("report")`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/cross-device/tests/test_cdp_timing.py harness/skills/workspace-verify/tests/test_ws_report.py -q`
Expected: 新增用例 FAIL

- [ ] **Step 3: 实现**

1. `cdp_timing.py` KNOWN_SEGMENTS 追加 `"report_post"`（注释：report mark 之后至收据落盘的尾部工作——content_tree/commit_scope/write_receipt/append_trend）。
2. `ws_report.py`：把 `_mark_report` 的直写实现重构为 `_append_direct_mark(timings_file, batch_id, name)`（原逻辑参数化 mark 名），`_mark_report` 改为调用它（name="report"）；content_tree 调用前（:707 附近 `verified_tree, commit_scope = "", ""` 之后、`try:` 之前）调用 `_append_direct_mark(args.timings_file, batch_id, "report_post")`。
3. `_resolve_timings` 调用块（:587-596，含 derived_elapsed 赋值）整体后移到 commit_scope try/except 之后、`r = Receipt(...)` 之前。前移带来的空缺处以注释说明（"链路耗时解析后移至收据尾部工作完成后，finish 段收窄为纯算段时刻、report_post 段覆盖 content_tree 等尾部开销"）。
4. 回归核对：`args.timings` 的消费点（Receipt timings 字段 :726、_trend_timing :735）均在后移点之后，顺序合法；`_resolve_cases`/验收证据门禁段（:600-693）不依赖 timings，无需移动。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_report.py harness/skills/cross-device/tests/test_cdp_timing.py -q`
Expected: 全绿（既有 timings 产物测试如因 finish 段收窄需微调断言，属预期适配，改动须在报告中列明）

- [ ] **Step 5: Commit**

```bash
git add harness/skills/cross-device/lib/python/cdp_timing.py harness/skills/workspace-verify/ws_report.py harness/skills/cross-device/tests/test_cdp_timing.py harness/skills/workspace-verify/tests/test_ws_report.py
git commit -m "新增(workspace-verify): report_post收编report尾部盲区并后移耗时解析"
```

---

### Task 4: B6 用例两级策略契约化（纯文档）

**Files:**
- Modify: `harness/config/verify-cases.yaml`（顶部注释）
- Modify: `harness/skills/cross-device/docs/cdp-contract.md`（验收语法区段）
- Modify: `harness/skills/cross-device/cross-device-emit/SKILL.md`（产批步骤 3 区段）

- [ ] **Step 1: verify-cases.yaml 顶部（`cases:` 之前）插入注释段**

```yaml
  # ── 用例两级策略（B6 契约化，emit 产批按此选择验收 case 集合）────────
  # 快速回归组（-sv 常态回归批默认）：lcview-liveness, lcview-pipeline,
  #   lcview-trigger, lciod-liveness, lciod-trigger（5 case，健康态 ~38-64s；
  #   覆盖存活/数据面/触发全链路）
  # 发布全量组（publish-main-base 前全量验收批）：全部 11 case（~120-190s）；
  #   perf/recover-daemon/transfer 含 IO 副作用或心跳周期绑架，不入常态轮次
  #   （见各 case 注释），发布前必须全量跑一轮
  # 批次方向涉及其它 case 的专项修复时按需追加（如 sepolicy 改动加
  #   lcview-sepolicy-label）
```

- [ ] **Step 2: cdp-contract.md 验收语法区段（`case:<id>` 语法说明附近）追加**

```markdown
**用例两级策略（B6）**：`-sv` 常态回归批验收 case 默认取快速回归组
（lcview-liveness, lcview-pipeline, lcview-trigger, lciod-liveness,
lciod-trigger）；publish-main-base 前的全量验收批取全部 case；批次方向
涉及特定 case 的专项修复按需追加。选择依据见 verify-cases.yaml 顶部注释。
```

- [ ] **Step 3: cross-device-emit/SKILL.md 产批步骤 3（:44 附近）追加一行**

```markdown
   验收 case 按两级策略选（B6）：常态回归取快速回归组 5 case；发布全量批取
   全部；专项修复按需追加——见 verify-cases.yaml 顶部注释与 cdp-contract
```

- [ ] **Step 4: 验证**

Run: `python3 -c "import yaml; d=yaml.safe_load(open('harness/config/verify-cases.yaml')); print('cases:', len(d['cases']), 'modules:', sorted(d['modules']))"`
Expected: `cases: 11 modules: ['lciod', 'lcview']`（yaml 加载不受注释影响）

Run: `python3 harness/lib/selfcheck.py`（refs 检查覆盖 docs 引用完整性）
Expected: refs_rc=0

- [ ] **Step 5: Commit**

```bash
git add harness/config/verify-cases.yaml harness/skills/cross-device/docs/cdp-contract.md harness/skills/cross-device/cross-device-emit/SKILL.md
git commit -m "文档(harness): 用例两级策略契约化快速回归组与发布全量组"
```

---

### Task 5: A2 重连快速失败预算（budget_s）

**Files:**
- Modify: `harness/skills/workspace-verify/ws_adb_connect.py`（ensure_connected 签名 :176 + CLI p_ensure :444 区段）
- Modify: `harness/skills/loop-engineering/SKILL.md`（步骤 4 失败轮区段 :82 附近）
- Test: `harness/skills/workspace-verify/tests/test_ws_adb_connect.py`

**背景**：acceptance 失败重跑轮重连 868s（150635 五次重连，connect#3 单次 346s）。编排层在失败轮先做一次带预算的廉价探测，预算耗尽快速失败（按 env_fail 归因），不进入三级发现链长等待。默认不传预算时行为完全不变。

- [ ] **Step 1: 写失败测试**

`test_ws_adb_connect.py` 追加（沿用文件既有 mock 风格）：

```python
class TestEnsureConnectedBudget(unittest.TestCase):
    """A2：ensure_connected 连接预算——失败轮编排层先做带预算的廉价探测，
    预算耗尽快速失败（env_fail 归因），不进三级发现链长等待。默认
    budget_s=None 行为完全不变。"""

    def test_budget_exhausted_fails_fast(self):
        # 预算已耗尽（budget_s=0 即 deadline 已过）→ 不进入 mDNS 等任何
        # 后续发现级，返回 None 快速失败
        with mock.patch.object(ac, "_adb_devices_online", return_value=[]), \
                mock.patch.object(ac, "mdns_discover",
                                  side_effect=AssertionError("不应进入 mDNS")):
            self.assertIsNone(ac.ensure_connected(budget_s=0))

    def test_budget_none_walks_discovery(self):
        # 不传预算 → 现行为不变：快路径未命中后仍进 mDNS 发现；
        # mDNS 空 + 静态 host_port=None + rescue 默认关 → 返回 None
        with mock.patch.object(ac, "_adb_devices_online", return_value=[]), \
                mock.patch.object(ac, "mdns_discover", return_value=[]) as md, \
                mock.patch.object(ac, "host_port", return_value=None):
            self.assertIsNone(ac.ensure_connected())
        md.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_adb_connect.py -q`
Expected: 新增用例 FAIL（`TypeError: ensure_connected() got an unexpected keyword argument 'budget_s'`）

- [ ] **Step 3: 实现**

1. `ensure_connected` 签名改为 `def ensure_connected(rescue_enabled=False, budget_s=None):`，docstring 追加一段：

```
    budget_s（A2）：连接预算秒数——失败轮编排层先做带预算的廉价探测，
    预算耗尽（monotonic 起算）即打印 [budget] 并返回 None 快速失败，不进
    入后续发现级（mDNS/静态/rescue）；None 时无预算，行为与旧版一致。
```

2. 函数体开头插入：

```python
    deadline = time.monotonic() + budget_s if budget_s is not None else None

    def _budget_left():
        return deadline is None or time.monotonic() < deadline
```

3. 各级入口卫语句：mDNS 循环前、静态尝试前、rescue 前分别插入：

```python
    if not _budget_left():
        print(f"[budget] 连接预算耗尽（{budget_s}s），快速失败")
        return None
```

（mDNS 候选循环内逐候选也应检查，避免单级内长等；快路径为廉价预检不设卫。）
4. 确认文件已 `import time`。
5. `main()` 的 p_ensure 子命令（:444 附近）追加 `p_ensure.add_argument("--budget", type=int, default=None, help="连接预算秒数（A2 快速失败；缺省无预算）")`，ensure 分支调用处透传 `budget_s=args.budget`。
6. `harness/skills/loop-engineering/SKILL.md` 步骤 4（失败轮分析修复重跑）追加指引：

```markdown
   重跑前先做带预算连接探测（A2）：python3 harness/skills/workspace-verify/
   ws_adb_connect.py ensure --budget 60 —— 预算内不在线按 env_fail 归因
   （砖机三分法）进入下一轮 patience，不进入验收长等待
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_adb_connect.py -q`
Expected: 全绿（含既有 37+ 用例——默认行为不变回归）

- [ ] **Step 5: Commit**

```bash
git add harness/skills/workspace-verify/ws_adb_connect.py harness/skills/workspace-verify/tests/test_ws_adb_connect.py harness/skills/loop-engineering/SKILL.md
git commit -m "新增(workspace-verify): ensure连接预算快速失败供失败轮廉价探测"
```

---

### Task 6: ws_verify_chain 编排串联（sync→push→unit_test）

**Files:**
- Create: `harness/skills/workspace-verify/ws_verify_chain.py`
- Modify: `harness/skills/workspace-verify/SKILL.md`（步骤 1-3 区段追加链式入口说明）
- Test: Create `harness/skills/workspace-verify/tests/test_ws_verify_chain.py`

**背景**：sync/push/unit_test 三步间为 AI 编排往返（收据 gap_before_verify_* 三段 ~55s/批，batch1 已收窄归因但时间仍在）。串联为单脚本一次执行：逐段透传 stdout、rc 逐段门禁、失败即停，输出自描述 JSON（run_id/逐段 rc/dur_s/overall）。acceptance 仍留编排层（参数动态、失败需 AI 归因）。

- [ ] **Step 1: 写失败测试**（新建 `harness/skills/workspace-verify/tests/test_ws_verify_chain.py`）

```python
# ws_verify_chain 单测：sync→push→unit_test 三步串联编排。关键场景：
# 全过顺序执行、中途失败即停（后续步骤不执行）、JSON 产物含逐段 rc 与耗时。

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_verify_chain as wc


class TestChain(unittest.TestCase):
    def _steps(self):
        return [("sync", ["python3", "sync.py"]),
                ("push", ["python3", "push.py"]),
                ("unit_test", ["python3", "tests.py"])]

    def test_all_pass_runs_in_order(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd[1])
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(wc.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(wc, "_CHAIN_STEPS", self._steps()), \
                mock.patch.object(wc.time, "monotonic", side_effect=[0, 1, 1, 2, 2, 3]):
            rc, result = wc.run_chain()
        self.assertEqual(rc, 0)
        self.assertEqual(result["overall"], "pass")
        self.assertEqual(calls, ["sync.py", "push.py", "tests.py"])
        self.assertEqual([s["rc"] for s in result["steps"]], [0, 0, 0])
        self.assertEqual([s["dur_s"] for s in result["steps"]], [1.0, 1.0, 1.0])

    def test_fail_stops_chain(self):
        def fake_run(cmd, **kw):
            return mock.Mock(returncode=(1 if cmd[1] == "push.py" else 0),
                             stdout="", stderr="boom")

        with mock.patch.object(wc.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(wc, "_CHAIN_STEPS", self._steps()), \
                mock.patch.object(wc.time, "monotonic", side_effect=[0, 1, 1, 2]):
            rc, result = wc.run_chain()
        self.assertEqual(rc, 1)
        self.assertEqual(result["overall"], "fail")
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "push"])  # unit_test 未执行
        self.assertIn("unit_test", result["skipped"])

    def test_result_file_written_with_run_id(self):
        with mock.patch.object(wc.subprocess, "run",
                               return_value=mock.Mock(returncode=0,
                                                      stdout="ok", stderr="")), \
                mock.patch.object(wc, "_CHAIN_STEPS", self._steps()), \
                mock.patch.dict("os.environ", {"CDP_RUN_ID": "run-xyz"}):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "chain.json"
                rc, _ = wc.run_chain(result_file=str(out))
                data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(data["run_id"], "run-xyz")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py -q`
Expected: FAIL（ModuleNotFoundError / ImportError: ws_verify_chain）

- [ ] **Step 3: 实现**（新建 `harness/skills/workspace-verify/ws_verify_chain.py`）

```python
#!/usr/bin/env python3
# ============================================================
# ws_verify_chain.py — 上板验证确定性三步串联（sync→push→unit_test）
# 所属模块：workspace-verify — 编译产物上板验证
# 设计目的：三步间原为 AI 编排往返（收据 gap_before_verify_* 三段 ~55s/批）。
#   本脚本把确定性步骤串联为单次执行：逐段透传 stdout、rc 逐段门禁、
#   失败即停（后续步骤不执行），末尾输出自描述 JSON（run_id/逐段 rc 与
#   耗时/overall/skipped）。打点仍由各子脚本自发 mark（verify_sync/
#   verify_push/verify_unit_test 段口径不变）；acceptance 留在编排层
#   （参数动态、失败需 AI 归因）。
# 用法：python3 ws_verify_chain.py [--product rpi5] [--out <aosp out>]
#   [--result-file <json>]
# 退出码：0 三步全过 / 1 某步失败（JSON 标注停在哪步）/ 2 参数错误
# ============================================================

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SYNC = _SCRIPT_DIR.parent / "sync-code-to-workspace" / "sync_code_to_workspace.py"

# 链式步骤表（可注入单测）：name → argv 模板
_CHAIN_STEPS = [
    ("sync", [sys.executable, str(_SYNC), "--auto"]),
    ("push", None),      # 运行时按 --product/--out 构造
    ("unit_test", None),
]


def run_chain(product="rpi5", out=None, result_file=None):
    """顺序执行三步，返回 (rc, result_dict)。失败即停，余步记入 skipped。"""
    push_cmd = [sys.executable, str(_SCRIPT_DIR / "ws_push.py"),
                "--product", product]
    unit_cmd = [sys.executable, str(_SCRIPT_DIR / "ws_upload_tests.py"),
                "--product", product]
    if out:
        push_cmd += ["--out", out]
        unit_cmd += ["--out", out]
    argv_map = {"sync": _CHAIN_STEPS[0][1], "push": push_cmd,
                "unit_test": unit_cmd}
    steps, skipped = [], []
    overall = "pass"
    for name, _ in _CHAIN_STEPS:
        t0 = time.monotonic()
        proc = subprocess.run(argv_map[name])
        steps.append({"name": name, "rc": proc.returncode,
                      "dur_s": round(time.monotonic() - t0, 3)})
        if proc.returncode != 0:
            overall = "fail"
            skipped = [n for n, _ in _CHAIN_STEPS
                       if n not in {s["name"] for s in steps}]
            break
    result = {"run_id": os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex,
              "steps": steps, "skipped": skipped, "overall": overall}
    if result_file:
        p = Path(result_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    return (0 if overall == "pass" else 1), result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="上板验证确定性三步串联（sync→push→unit_test）")
    ap.add_argument("--product", default="rpi5")
    ap.add_argument("--out", default=None, help="AOSP out 目录（透传）")
    ap.add_argument("--result-file", default=None,
                    help="自描述链式产物 JSON（原子写）")
    args = ap.parse_args(argv)
    rc, result = run_chain(args.product, args.out, args.result_file)
    print(json.dumps(result, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
```

（实现者按测试驱动微调：测试里 `_CHAIN_STEPS` 被 patch 为 [(name, cmd)] 元组列表且 time.monotonic 序列可控——实现须与之自洽，如 per-step t0/t1 各取一次 monotonic。）

`workspace-verify/SKILL.md` 步骤 1 区段（"1. 同步：…" 之前）追加：

```markdown
0b. 链式入口（推荐，减编排往返）：三步确定性环节可单脚本串联——
    python3 harness/skills/workspace-verify/ws_verify_chain.py --result-file <chain.json>
    逐段 stdout 透传、rc 逐段门禁、失败即停（JSON 标注停在何步）；成功后
    直跳步骤 4b/5。失败时按原分步工作流重跑定位，各段打点口径不变。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py -q`
Expected: PASS（3 用例）

- [ ] **Step 5: Commit**

```bash
git add harness/skills/workspace-verify/ws_verify_chain.py harness/skills/workspace-verify/tests/test_ws_verify_chain.py harness/skills/workspace-verify/SKILL.md
git commit -m "新增(workspace-verify): verify_chain串联确定性三步减编排往返"
```

---

### Task 7: C3 loop 修复编辑入账（打点指引，纯文档）

**Files:**
- Modify: `harness/skills/loop-engineering/SKILL.md`（步骤 4 失败轮区段）
- Modify: `harness/skills/cross-device/cross-device-apply/SKILL.md`（:63-64 区段）

- [ ] **Step 1: loop-engineering/SKILL.md 步骤 4（"修复编辑 code/ → 重跑"处）追加**

```markdown
   修复编辑打点（C3，与 B1 selfcheck 收口联动）：编辑开始前 mark
   edit_plan（同名自动 #N 序号）标记修复编辑起点；编辑完成无需手动收口
   ——selfcheck 开跑前自动补打 edit（同名 #N），修复编辑耗时计入 edit 相
   （此前散落 gap/other 不可归因）
```

- [ ] **Step 2: cross-device-apply/SKILL.md :64（"编辑完成打点（必做）… edit"）之后追加说明行**

```markdown
   （loop 收敛轮的修复编辑同契约：开始前 mark edit_plan，完成由 selfcheck
    自动收口为 edit#N，无需手动 mark edit）
```

- [ ] **Step 3: 验证**

Run: `python3 harness/lib/selfcheck.py`（refs_rc=0 确认 docs 引用完整）
Expected: refs_rc=0

- [ ] **Step 4: Commit**

```bash
git add harness/skills/loop-engineering/SKILL.md harness/skills/cross-device/cross-device-apply/SKILL.md
git commit -m "文档(harness): loop修复编辑入账edit相打点契约与selfcheck收口联动"
```

---

### Task 8: KI-20260902-001 闭环（KIR-006，依赖 Task 1 commit sha）

**Files:**
- Modify: `data/known-issues/index.md`
- Modify: `data/known-issues/20260902-163633-e656cfe14c13-cdp_timing-start-时机由-apply-自定致-edit-段耗时失.md`

- [ ] **Step 1: 取 Task 1 commit sha**（`git log --oneline` 中 "selfcheck自动edit收口" commit）

- [ ] **Step 2: 更新两文件**

index.md 行改为：
```
KI-20260902-001 pre-existing false cdp-timing-start fixed
```

详情文件 header：`- status: fixed`、`- resolved_in: <Task 1 commit sha>`；正文末尾追加：

```markdown
## 闭环记录（2026-09-04，KIR-006）

修法落地：selfcheck 开跑前自动判定编辑是否收口，未收口补打 mark edit
（同名自动 #N）——编辑区间口径不再依赖 AI 自判，loop 轮修复编辑同契约
入账（C3 联动）。闭环后 edit 段跨批可比（-s 批漂移形态由补打归口）。
按 KIR-006 终态不写时老化，待 promote 清算删除。
```

- [ ] **Step 3: Commit**

```bash
git add data/known-issues/
git commit -m "文档(known-issues): KI-20260902-001闭环edit收口制度化落地"
```

---

### Task 9: 收口验证（自动执行，结果进最终报告）

- [ ] **Step 1: 全仓自检**：`python3 harness/lib/selfcheck.py` → pytest_rc=0、refs/config/contract 全 0
- [ ] **Step 2: 真机冒烟**（设备 192.168.1.28，只读/跳过型操作）：
  - `ws_adb_connect.py ensure --budget 60` 预算探测
  - `ws_verify_chain.py` 真跑（sync 空 plan → push 全 SKIP 幂等 → 单测真跑）核对 JSON 输出
  - `ws_acceptance.py run --case lciod-liveness`（1.2s 无副作用）核对验收链路
- [ ] **Step 3: 汇总最终验证报告**（含 batch1+2+3 全量 commit 清单与指标核对）

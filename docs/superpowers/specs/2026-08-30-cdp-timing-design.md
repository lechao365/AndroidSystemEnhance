# cross-device-apply 链路耗时打点设计

- 日期：2026-08-30
- 状态：待评审
- 范围：新增 `harness/skills/cross-device/lib/python/cdp_timing.py`；`cdp_paths.py`/`cdp_receipt.py`/`ws_report.py` 小改；cross-device-apply、workspace-verify、cross-device-emit 三个 SKILL.md 打点接入；docs/cdp-contract.md 同步。不改既有脚本行为契约（timings 全程可选、降级 warn 不阻断）。

## 1. 背景与目标

### 1.1 现状问题（调查实证）

| # | 问题 | 证据 |
|---|------|------|
| P1 | 链路耗时从未被记录，收据 `elapsed_s` 几乎全为 0 | 20 份收据仅 4 份非 0；`ws_report.py` 有 `--elapsed` 参数但 apply/verify SKILL 步骤 6 均未传 |
| P2 | 无阶段粒度：即使记录也只知总时长，无法定位是编译/单测/验收哪一段慢 | 批次接收→收据落盘推算 -sv 耗时 5.5~81 分钟波动，构成未知 |
| P3 | emit 复盘只有失败现场可用，缺耗时维度给修复方案 | emit SKILL 上下文组装只读"最新收据（失败现场摘录）" |

### 1.2 目标

1. apply 链路粗粒度打点：precheck / edit / verify / receipt / push 各段耗时
2. verify 内部细粒度打点：sync / build / push / unit_test / acceptance（loop 多轮按轮次记录）
3. 打点数据随收据落盘 `data/verify-results`（header `timings` 字段），emit 侧可读
4. 打点全程可选：缺失/非法仅 warn，不阻断 push 主流程

## 2. 架构与数据流

```
AI: 批次接收 → cdp_timing start → precheck mark → edit mark
   → verify（内部 sync/build/push/unit_test/acceptance 各自 mark）→ verify_end
   → ws_report --timings-file <file> → 收据 timings 字段 → git-works-push
emit: 读最新收据 timings 字段 → 定位耗时瓶颈 → 复盘/下批修复方向
```

打点文件（工作态，gitignore）：`harness/log/cross-device-apply/timings-<batch_id>.json`
收据（持久态）：`data/verify-results/<ts>-<batch_id>.md` header `timings` 字段

## 3. 组件设计

### 3.1 cdp_timing.py（新增）

`harness/skills/cross-device/lib/python/cdp_timing.py`，apply/verify 共用。

| 子命令 | 行为 | 退出码 |
|--------|------|--------|
| `start --batch <12hex>` | 初始化打点文件 `timings-<batch_id>.json`，记录 `start_wall`（epoch 秒小数） | 0；已存在则覆盖重建；缺 --batch 返 2 |
| `mark --name <阶段名>` | 追加 `{name, wall}`（epoch 秒小数） | 0；未 start 返 3 |
| `finish [--file <path>]` | 由 start 与 mark 序列计算相邻段耗时，落盘 JSON 数组 `[{name, elapsed_s}]`（仅含 >=1 段，首段为 start→首个 mark，末段为末 mark→finish 时刻） | 0；未 start 返 3；无 mark 输出空数组仍 0 |

- 文件路径经 `cdp_paths.log_apply_dir()`（`harness/log/cross-device-apply/`，gitignore 工作态），不硬编码。
- finish 输出结构：`{"batch_id":..., "wall_start":..., "wall_end":..., "segments":[{"name":..., "elapsed_s":...}]}`。
- 容错：mark/finish 对未 start 返非 0（AI 漏 start 可发现）；缺段不崩（finish 只输出已有段）。
- 原子写：临时文件 + replace（对齐 append_trend 惯例）。

### 3.2 cdp_paths.py（小改）

新增 `log_apply_dir()`：`project_root()/harness/log/cross-device-apply`，mkdir，供打点文件定位。

### 3.3 cdp_receipt.py（小改）

`_FIELDS` 在 `metrics` 之后追加 `"timings"`；`Receipt.__init__` 加 `timings=""`。
向后兼容：旧收据无该字段，`from_text` 缺省空串；不升 schema 版本（字段可选）。

### 3.4 ws_report.py（小改）

新增 `--timings-file <path>`（可选）：读取并校验为合法 JSON 数组，规范化（排序）后写入收据 `timings` 字段。
- 文件不存在或非法 JSON：**warn 降级**，`timings` 置空，不返 2（timings 是诊断数据非验收证据，区别于 `--acceptance` 的返 2 拒写）。
- 不进 trend.md（数据量大，trend 只留性能指标 `metrics`）。

### 3.5 apply SKILL.md 打点接入

- precheck（步骤 3）后：`cdp_timing.py start --batch <batch_id>`（batch_id 取 precheck 输出）。
- 各步骤 mark：`precheck`（precheck 完成）→ `edit`（编辑+validate_patch+gen_manifest 完成）→ `verify_start`（-sv 拉起前）→ verify 内部由 verify 侧 mark → `verify_end`（verify 返回后）→ `receipt`（收据落盘后）→ `push`（git-works-push 完成后）。
- 步骤 6 `ws_report` 调用追加 `--timings-file harness/log/cross-device-apply/timings-<batch_id>.json`。
- -s 批次同样打点（无 verify 内部段）。

### 3.6 workspace-verify SKILL.md 打点接入

- 模式 A（--batch-file）时打点文件已由 apply 侧 start：verify 侧 AI 在阶段切换 mark：
  `verify_sync`（步骤 1）→ `verify_build`（步骤 3）→ `verify_push`（步骤 4）→ `verify_unit_test`（步骤 4b）→ `verify_acceptance`（步骤 5）→ `verify_receipt`（步骤 6）。
- loop 多轮：每轮 mark 名带轮次前缀 `run_<n>_<stage>`，区分重试轮。
- 模式 B（独立触发，无批次）：可选 start 独立打点（batch 用 `manual-<ts>`），不强制。
- 打点文件定位：batch_id 从 `--batch-file` 经 `cdp_parse.batch_id_from_text` 解析；文件不存在则跳过打点（warn 不阻断）。

### 3.7 emit SKILL.md 消费

- 上下文组装步骤 2 增加：读最新收据 `timings` 字段，定位耗时异常阶段（如 build 占比过高、重试轮数多），作为复盘依据。
- 复盘产出中"上批复盘逐条取证"补充耗时维度。

### 3.8 docs/cdp-contract.md 同步

补收据 `timings` 字段说明（格式、来源、可选语义）。

## 4. 测试

- 新增 `tests/test_cdp_timing.py`：start/mark/finish 全流程、段耗时计算正确性、未 start 报错、缺 mark 容错、路径含 batch_id、原子写。
- `tests/test_cdp_receipt.py` 补：timings 字段写读 roundtrip、旧收据（无 timings 字段）解析回落空串。
- `ws_report.py` timings-file 校验：合法数组写入、非法 warn 不阻断（单测）。
- 验证命令：`python3 -m pytest harness/skills/cross-device/tests/ -q`；`python3 harness/lib/check_skill_refs.py`（防 SKILL 悬空引用）。

## 5. 边界与容错

| 场景 | 行为 |
|------|------|
| AI 漏 start | mark/finish 返非 0，AI 可发现；不写收据 timings |
| finish 无 mark | 输出空 segments，不崩 |
| --timings-file 缺失/非法 | ws_report warn，timings 置空，不阻断 push |
| 旧收据无 timings | from_text 回落空串，不崩 |
| loop 多轮 | 轮次前缀 `run_<n>_<stage>` 区分 |

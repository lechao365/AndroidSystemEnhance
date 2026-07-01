---
name: lc-revert-code-from-patchs
description: 仅以 promoted baseline 为真相源，把 workspace 偏离部分拉回一致（计划生成 → AI 逐条确认 → 执行 → 落盘校验）。
stages:
  - research: "AI 分析 workspace diff/上下文"
  - plan: "AI 生成实施计划，经用户确认"
  - code: "执行具体操作"
  - review: "验证结果并提交"
---

# lc-revert-code-from-patchs

将 `~/workspace/` 中偏离 `patchs/rpi5/` 基线的部分**拉回一致**，用于在 workspace 改坏后回到上次归档的可工作状态。

**核心语义**：`patchs/rpi5/` 是 workspace 定制改动的**已知良好基线**（真相源）。本工作流是 `lc-sync-code-to-patchs` 的逆操作：sync 是 workspace→patchs 归档，revert 是 patchs→workspace 回退。

> **与 source-code-modify.md 的关系**：本工作流是该规则"workspace 是源头"原则的**受控例外**——当 workspace 处于不可用的坏状态时，允许反向把 patchs 状态写回 workspace。不改变日常归档流程，仅作灾难恢复。

## Trigger（触发条件）

- workspace 处于不可用的坏状态，需要回到上次归档的可工作状态
- 用户请求"回退"/"revert"/"拉回 patchs 基线"

## Preconditions（前置条件）

1. **真相源资格**：本 workflow 使用的 patchs 基线必须是 **promoted baseline**（已完成晋升，证据完整）。未证据化 baseline 不得作为恢复真相源（`SRC-004`）。证据字段以 `engineering/harness/config/baseline-evidence-template.yaml` 为模板，状态登记维护在 `engineering/harness/config/baseline-status.yaml`。若 patchs 资产未完成晋升，本 workflow **拒绝执行**，提示用户先走 lc-sync-code-to-patchs + 晋升流程。
2. 操作对象仅限 `~/workspace/`（kernel + aosp），**不动 `patchs/`**
3. 执行前建议 `git stash`/commit 保存当前坏状态现场（脚本不自动备份，便于事后定位根因）
4. **不自动 `git add`/`git commit`**：执行后 working tree 处于回退后状态，由用户决定是否提交（便于 `git diff` 复查）
5. workspace 各 git 仓库已配置 upstream base（否则脚本退出码 3，不猜测任意 remote）

## Inputs（输入）

| 参数 | 说明 |
|------|------|
| 无参数 | 生成回退计划到 artifacts |
| `--plan-file <path>` | 指定 plan 路径（默认 `engineering/output/log/revert_code_from_patchs/artifacts/<ts>-plan.tsv`，`OBS-002`） |
| `--check-only` | 仅预览，不生成 plan |
| `--apply --plan-file <path>` | 执行 plan 中 `+` 标记的条目（apply 模式） |

## Human confirmation gates（人工确认门）

**两道确认门（强制）**：

1. **逐条确认门**（Step 2）：AI 按 `MODIFIED-DIVERGED` / `NEW-MISMATCH` / `EXTRA` 分类呈现 plan，用户逐条/逐类指示执行或跳过。AI 直接编辑 plan 文件的 `+`/`-` 标记与动作字段。
2. **最终汇总确认门**（Step 3）：AI 展示选中条目汇总（各类数量 + 动作分布），等用户最终 `y` 确认后才能执行 `--apply`。

**禁止**跳过逐条确认直接 apply；**禁止**将 plan 生成误认为已确认。

## Outputs / artifacts（输出/产物）

- 回退后的 workspace working tree（不自动 commit）
- plan 文件：`engineering/output/log/revert_code_from_patchs/artifacts/<ts>-plan.tsv`
- 校验文件：`engineering/output/log/revert_code_from_patchs/artifacts/<ts>-verify.tsv`（apply 后强制全量扫描）
- 逐条执行结果（`[CHECKOUT]` / `[RESTORE]` / `[REVERT]` 动作打印 + 退出码）
- 日志/artifacts 按 `OBS-001`/`OBS-002` 落盘

**落盘校验（强制，全量，apply 后自动执行）**——apply 完成后脚本自动重跑全量扫描，与原 plan 对比，分 4 类输出：

| 标记 | 含义 | 是否算失败 |
|------|------|-----------|
| ✅ `FIXED` | 原 `+` 执行的条目现已是 MATCH | 否（成功） |
| ⚠ `KEPT` | 原 `-` skip 的条目仍偏离 | 否（用户主动保留） |
| ❌ `RESIDUAL` | 原 `+` 执行的条目仍偏离 | **是（回退未生效）** |
| ❓ `NEW-DIFF` | apply 后新出现的差异 | **是（需排查）** |

退出码：有 `RESIDUAL` 或 `NEW-DIFF` → 非 0（失败）；仅 `KEPT` → 0（成功）。

## Failure / recovery（失败/恢复）

| 场景 | 处理 |
|------|------|
| patchs 资产未晋升为 promoted baseline（`SRC-004`） | 拒绝执行，提示先走 sync + 晋升流程 |
| workspace 不存在 | 报错退出 |
| 无法确定 upstream base | 报错退出（`harness_report_no_upstream` 输出当前分支与 `git branch --set-upstream-to=` 修复建议），不再猜测任意 remote |
| patchs 为空 | 报错退出 |
| `.diff` 损坏（`git apply --check` 失败） | 标记 BROKEN-DIFF，该条 return 1 停止执行 |
| apply 失败 | **立即停止**，退出码非 0（避免半完成状态） |
| EXTRA 命中编译产物 | 不列入 EXTRA（排除规则） |
| workspace 有 staged 改动 | 仅警告不阻断 |
| 校验有 RESIDUAL/NEW-DIFF | 退出码非 0 |

## Related policy IDs（关联规则 ID）

- `SRC-001`：workspace 是日常源码真相源（本 workflow 是受控例外）
- `SRC-002`：patchs 单向受控归档（revert 不写 patchs，只读 patchs 基线）
- `SRC-004`：未证据化 promoted baseline 不得作为恢复真相源（**强制前置**）
- `OBS-001` / `OBS-002`：脚本维测（模式 A、统一退出码、plan/verify 产物归档）

---

## 前置约束

1. 操作对象仅限 `~/workspace/`（kernel + aosp），**不动 `patchs/`**
2. 执行前建议 `git stash`/commit 保存当前坏状态现场（脚本不自动备份，便于事后定位根因）
3. **不自动 `git add`/`git commit`**：执行后 working tree 处于回退后状态，由用户决定是否提交（便于 `git diff` 复查）

## 工作流（6 步闭环）

### 1. 生成回退计划（脚本）

```bash
bash engineering/harness/workflows/lc-revert-code-from-patchs/revert_code_from_patchs.sh              # 生成 plan 到 artifacts
bash engineering/harness/workflows/lc-revert-code-from-patchs/revert_code_from_patchs.sh --plan-file X # 指定 plan 路径
bash engineering/harness/workflows/lc-revert-code-from-patchs/revert_code_from_patchs.sh --check-only  # 仅预览，不生成 plan
```

plan 默认输出路径：`engineering/output/log/revert_code_from_patchs/artifacts/<ts>-plan.tsv`，不再写到 `/tmp/`。可用 `--plan-file <path>` 指定外部路径。

脚本扫描 workspace 与 patchs 差异，输出五类分类：

| 类别 | 含义 | 是否列入 plan |
|------|------|--------------|
| `MODIFIED-MATCH` | patchs 有 modified，workspace 当前 = patchs | ❌ 仅汇总 |
| `NEW-MATCH` | patchs 有 new，workspace 存在且内容 = patchs | ❌ 仅汇总 |
| `MODIFIED-DIVERGED` | patchs 有 modified，workspace 当前 ≠ patchs | ✅ 逐条 |
| `NEW-MISMATCH` | patchs 有 new，workspace 缺失或内容 ≠ patchs | ✅ 逐条 |
| `EXTRA` | workspace 有改动但 patchs 未覆盖（坏改动/调试代码） | ✅ 逐条 |

### 2. AI 主持逐条确认（详见上方 Human confirmation gates）

AI 读取生成的 plan 文件（TSV），按类别分组呈现给用户：

- **MODIFIED-DIVERGED**：列出文件 + 差异摘要，默认动作 `checkout`（拉回 patchs）
- **NEW-MISMATCH**：列出文件 + 缺失/不一致状态，默认动作 `restore`
- **EXTRA**：列出文件 + 来源描述，默认动作 `revert`

用户逐条/逐类指示：
- "这条 skip" / "这组全选" / "这条改成 checkout-only"
- AI **直接编辑 plan 文件**的 `+`（执行）/ `-`（跳过）标记和动作字段

### 3. 最终确认 + 执行（详见上方 Human confirmation gates）

AI 展示选中条目汇总（各类数量 + 动作分布），等用户最终 `y` 确认后执行：

```bash
bash .../revert_code_from_patchs.sh --apply --plan-file <plan路径>
```

脚本行为：
- 只执行 `+` 标记的条目
- 每条执行前打印动作（`[CHECKOUT] kernel:drivers/...`）
- **失败立即停止**（退出码非 0），避免半完成状态

### 4. 落盘校验（强制，全量）

（校验矩阵见上方 Outputs / artifacts）

落盘文件：`engineering/output/log/revert_code_from_patchs/artifacts/<ts>-verify.tsv`

### 5. 执行结果报告

AI 汇报各类执行数量 + 校验结果。若校验失败，列出 RESIDUAL/NEW-DIFF 条目供排查。

### 6. 后续（不自动）

- 提示用户 `git diff` 复查回退后的 working tree
- 由用户决定是否编译验证（`make bootimage` 等）
- **不自动 git commit**——是否提交由用户决定

## 动作矩阵

| 动作 | 适用类别 | 语义 | 具体 git 操作 |
|------|---------|------|-------------|
| `checkout` | MODIFIED-DIVERGED | 拉回 patchs | `git checkout $BASE -- $f && git apply patchs.diff` |
| `checkout-only` | MODIFIED-DIVERGED | 移除定制 | `git checkout $BASE -- $f` |
| `restore` | NEW-MISMATCH | 从 patchs 补回 | `cp patchs/new/... workspace` |
| `revert` | EXTRA-MODIFIED / EXTRA-NEW-TRACKED | 恢复 upstream | `git checkout $BASE -- $f` |
| `revert` | EXTRA-NEW-UNTRACKED | 删除 | `rm -f $f` |
| `skip` | 任意 | 不动 | — |
| `stash-hint` | EXTRA | 提示用户手动 stash | —（不执行） |

## plan 文件格式（TSV）

```
# REVERT-PLAN generated at <时间戳>
# 格式: <标记>\t<类别>\t<项目>\t<相对路径>\t<动作>\t<差异摘要>

+	MODIFIED-DIVERGED	kernel	drivers/usb/storage/transport.c	checkout	workspace diff 与 patchs 不一致
-	MODIFIED-DIVERGED	aosp:device/brcm/rpi5	device.mk	skip	仅注释差异，保留现状
+	NEW-MISMATCH	kernel	vendor/lechao/LcView/lcview_main.c	restore	workspace 缺失
+	EXTRA-MODIFIED	kernel	drivers/input/mouse/elog.c	revert	未归档的 upstream 文件改动
+	EXTRA-NEW-UNTRACKED	aosp:vendor/lechao	vendor/lechao/debug_temp.c	revert	非 repo 目录未归档文件
```

- **标记**：`+` 执行 / `-` 跳过（AI 确认时编辑）
- **项目**：`kernel` / `aosp` / `aosp:<repo_project>`（精确到 repo 才能正确 checkout）
- **相对路径**：相对 workspace 项目根

## 不做的事（YAGNI）

- 不自动 `git add`/`git commit`
- 不处理 `patchs/others/`（仅 kernel/aosp，`SRC-003`）
- 不做反向 patch（用 `git checkout` 更可靠）
- 不做多平台（仅 rpi5，未来扩展另立）

## 退出码
| 退出码 | 含义 | 下一步 |
|--------|------|--------|
| 0 | 成功 | 正常继续 |
| 1 | 脚本逻辑错误 | 检查日志 |
| 3 | 环境缺失 | 安装依赖后重试 |

## TODO 跟踪
- [ ] Step 1: 分析问题
- [ ] Step 2: 生成 plan
- [ ] Step 3: 用户确认
- [ ] Step 4: 执行
- [ ] Step 5: 验证

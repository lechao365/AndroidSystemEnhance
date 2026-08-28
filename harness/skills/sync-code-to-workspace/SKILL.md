---
name: sync-code-to-workspace
description: 把 workspace 偏离 code/rpi5 基线的部分拉回一致（计划生成 -> AI 逐条确认 -> 执行 -> 落盘校验；--auto 单进程闭环无确认门）。
no_commit: true
stages:
  - research: "AI 分析 workspace diff/上下文"
  - plan: "AI 生成实施计划，经用户确认"
  - code: "执行具体操作"
  - review: "验证结果并提交"
---

# sync-code-to-workspace

将 `~/workspace/` 中偏离 `code/rpi5/` 基线的部分**拉回一致**，用于在 workspace 改坏后回到上次归档的可工作状态。

**核心语义**：`code/rpi5/` 是 workspace 定制改动的**已知良好基线**（真相源）。本工作流是 `sync-workspace-to-code` 的逆操作：sync-workspace-to-code 是 workspace→code 归档，sync-code-to-workspace 是 code→workspace 同步。

> **与 source-code-modify.md 的关系**：本工作流是该规则"workspace 是源头"原则的**受控例外**——当 workspace 处于不可用的坏状态时，允许反向把 code 状态写回 workspace。不改变日常归档流程，仅作灾难恢复。

## Trigger（触发条件）

- workspace 处于不可用的坏状态，需要回到上次归档的可工作状态
- 用户请求"sync-code-to-workspace"/"同步 code 到 workspace"

## Preconditions（前置条件）

1. **真相源资格**：以 code 仓 dev/main HEAD 为恢复真相源（交互模式与 --auto 一致放宽，不再强制 promoted baseline；证据字段模板见 `harness/config/baseline-evidence-template.yaml`，状态登记在 `harness/config/baseline-status.yaml`；恢复真相源约束由 `SRC-004` 在规则层表达）。
2. 操作对象仅限 `~/workspace/`（kernel + aosp），**不动 `code/`**
3. 执行前建议 `git stash`/commit 保存当前坏状态现场（脚本不自动备份，便于事后定位根因）
4. **不自动 `git add`/`git commit`**：执行后 working tree 处于同步后状态，由用户决定是否提交（便于 `git diff` 复查）
5. workspace 各 git 仓库已配置 upstream base（否则脚本退出码 3，不猜测任意 remote）

## Inputs（输入）

| 参数 | 说明 |
|------|------|
| 无参数 | 生成同步计划到 tempfile（`--plan-file` 可指定持久路径） |
| `--plan-file <path>` | 指定 plan 路径（默认写入 harness/log/ 下 artifacts 目录） |
| `--check-only` | 仅预览，不生成 plan |
| `--apply --plan-file <path>` | 执行 plan 中 `+` 标记的条目（apply 模式） |
| `--auto` | 单进程闭环（生成→全选→执行→校验），无确认门 |

## Human confirmation gates（人工确认门）

**仅交互模式（不带 `--auto`）两道确认门（强制）**：

1. **逐条确认门**（Step 2）：AI 按 `MODIFIED-DIVERGED` / `NEW-MISMATCH` / `EXTRA` 分类呈现 plan，用户逐条/逐类指示执行或跳过。AI 直接编辑 plan 文件的 `+`/`-` 标记与动作字段。
2. **最终汇总确认门**（Step 3）：AI 展示选中条目汇总（各类数量 + 动作分布），等用户最终 `y` 确认后才能执行 `--apply`。

**`--auto` 模式**：单进程闭环（生成→全选→执行→落盘校验），无确认门。

**禁止**（交互模式）跳过逐条确认直接 apply；**禁止**将 plan 生成误认为已确认。

## Outputs / artifacts（输出/产物）

- 同步后的 workspace working tree（不自动 commit）
- plan 文件：默认写入 `harness/log/sync_code_to_workspace/artifacts/`（`--plan-file` 可指定持久路径）
- 校验文件：默认写入 `harness/log/sync_code_to_workspace/artifacts/`（apply 后强制全量扫描）
- 逐条执行结果（`[CHECKOUT]` / `[RESTORE]` / `[SYNC]` 动作打印 + 退出码）
- 日志输出到 stderr

**落盘校验（强制，全量，apply 后自动执行）**——apply 完成后脚本自动重跑全量扫描，与原 plan 对比，分 4 类输出：

| 标记 | 含义 | 是否算失败 |
|------|------|-----------|
| ✅ `FIXED` | 原 `+` 执行的条目现已是 MATCH | 否（成功） |
| ⚠ `KEPT` | 原 `-` skip 的条目仍偏离 | 否（用户主动保留） |
| ❌ `RESIDUAL` | 原 `+` 执行的条目仍偏离 | **是（同步未生效）** |
| ❓ `NEW-DIFF` | apply 后新出现的差异 | **是（需排查）** |

退出码：有 `RESIDUAL` 或 `NEW-DIFF` → 非 0（失败）；仅 `KEPT` → 0（成功）。

## Failure / recovery（失败/恢复）

| 场景 | 处理 |
|------|------|
| code 仓无 HEAD | 报错退出（无法以 dev/main 为真相源） |
| workspace 不存在 | 报错退出 |
| 无法确定 upstream base | 报错退出（脚本输出当前分支与 `git branch --set-upstream-to=` 修复建议），不再猜测任意 remote |
| code 为空 | 报错退出 |
| `.diff` 损坏（`git apply --check` 失败） | 标记 BROKEN-DIFF，该条 return 1 停止执行 |
| apply 失败 | **立即停止**，退出码非 0（避免半完成状态） |
| EXTRA 命中编译产物 | 不列入 EXTRA（排除规则） |
| workspace 有 staged 改动 | 仅警告不阻断 |
| 校验有 RESIDUAL/NEW-DIFF | 退出码非 0 |

## Related policy IDs（关联规则 ID）

- `SRC-001`：workspace 是日常源码真相源（本 workflow 是受控例外）
- `SRC-002`：code 单向受控归档（本 workflow 不写 code，只读 code 基线）
- `SRC-004`：未证据化 promoted baseline 不得作为恢复真相源（**强制前置**）

---

## 前置约束

1. 操作对象仅限 `~/workspace/`（kernel + aosp），**不动 `code/`**
2. 执行前建议 `git stash`/commit 保存当前坏状态现场（脚本不自动备份，便于事后定位根因）
3. **不自动 `git add`/`git commit`**：执行后 working tree 处于同步后状态，由用户决定是否提交（便于 `git diff` 复查）

## 工作流（6 步闭环）

### 1. 生成同步计划（脚本）

```bash
python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py              # 生成 plan 到 tempfile
python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --plan-file X # 指定 plan 路径
python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --check-only  # 仅预览，不生成 plan
```

plan 默认写入 `harness/log/sync_code_to_workspace/artifacts/`（`--plan-file` 可指定持久路径）。

脚本扫描 workspace 与 code 差异，输出五类分类：

| 类别 | 含义 | 是否列入 plan |
|------|------|--------------|
| `MODIFIED-MATCH` | code 有 modified，workspace 当前 = code | ❌ 仅汇总 |
| `NEW-MATCH` | code 有 new，workspace 存在且内容 = code | ❌ 仅汇总 |
| `MODIFIED-DIVERGED` | code 有 modified，workspace 当前 ≠ code | ✅ 逐条 |
| `NEW-MISMATCH` | code 有 new，workspace 缺失或内容 ≠ code | ✅ 逐条 |
| `EXTRA` | workspace 有改动但 code 未覆盖（坏改动/调试代码） | ✅ 逐条 |

### 2. AI 主持逐条确认（详见上方 Human confirmation gates）

AI 读取生成的 plan 文件（TSV），按类别分组呈现给用户：

- **MODIFIED-DIVERGED**：列出文件 + 差异摘要，默认动作 `checkout`（拉回 code）
- **NEW-MISMATCH**：列出文件 + 缺失/不一致状态，默认动作 `restore`
- **EXTRA**：列出文件 + 来源描述，默认动作 `sync`

用户逐条/逐类指示：
- "这条 skip" / "这组全选" / "这条改成 checkout-only"
- AI **直接编辑 plan 文件**的 `+`（执行）/ `-`（跳过）标记和动作字段

### 3. 最终确认 + 执行（详见上方 Human confirmation gates）

AI 展示选中条目汇总（各类数量 + 动作分布），等用户最终 `y` 确认后执行：

```bash
python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --apply --plan-file <plan路径>
```

脚本行为：
- 只执行 `+` 标记的条目
- 每条执行前打印动作（`[CHECKOUT] kernel:drivers/...`）
- **失败立即停止**（退出码非 0），避免半完成状态

### 4. 落盘校验（强制，全量）

（校验矩阵见上方 Outputs / artifacts）

落盘文件：默认写入 `harness/log/sync_code_to_workspace/artifacts/`

### 5. 执行结果报告

AI 汇报各类执行数量 + 校验结果。若校验失败，列出 RESIDUAL/NEW-DIFF 条目供排查。

### 6. 后续（不自动）

- 提示用户 `git diff` 复查同步后的 working tree
- 由用户决定是否编译验证（`make bootimage` 等）
- **不自动 git commit**——是否提交由用户决定

## 动作矩阵

| 动作 | 适用类别 | 语义 | 具体 git 操作 |
|------|---------|------|-------------|
| `checkout` | MODIFIED-DIVERGED | 拉回 code | `git checkout $BASE -- $f && git apply code.diff` |
| `checkout-only` | MODIFIED-DIVERGED | 移除定制 | `git checkout $BASE -- $f` |
| `restore` | NEW-MISMATCH | 从 code 补回 | `cp code/new/... workspace` |
| `sync` | EXTRA-MODIFIED / EXTRA-NEW-TRACKED | 恢复 upstream | `git checkout $BASE -- $f` |
| `sync` | EXTRA-NEW-UNTRACKED | 删除 | `rm -f $f` |
| `skip` | 任意 | 不动 | — |
| `stash-hint` | EXTRA | 提示用户手动 stash | —（不执行） |

## plan 文件格式（TSV）

```
# SYNC-PLAN generated at <时间戳>
# 格式: <标记>\t<类别>\t<项目>\t<相对路径>\t<动作>\t<差异摘要>

+	MODIFIED-DIVERGED	kernel	drivers/usb/storage/transport.c	checkout	workspace diff 与 code 不一致
-	MODIFIED-DIVERGED	aosp:device/brcm/rpi5	device.mk	skip	仅注释差异，保留现状
+	NEW-MISMATCH	kernel	vendor/lechao/LcView/lcview_main.c	restore	workspace 缺失
+	EXTRA-MODIFIED	kernel	drivers/input/mouse/elog.c	sync	未归档的 upstream 文件改动
+	EXTRA-NEW-UNTRACKED	aosp:vendor/lechao	vendor/lechao/debug_temp.c	sync	非 repo 目录未归档文件
```

- **标记**：`+` 执行 / `-` 跳过（AI 确认时编辑）
- **项目**：`kernel` / `aosp` / `aosp:<repo_project>`（精确到 repo 才能正确 checkout）
- **相对路径**：相对 workspace 项目根

## 已知限制

- **vendor/lechao 扫描盲区**：`vendor/lechao` 为独立 git 项目，但未登记于 `.repo/project.list`
  （该文件仅含 repo 管理的项目，vendor 下只有 `vendor/brcm`）。两条 `_scan_extra_aosp`
  路径均不覆盖它：
  * `_scan_extra_aosp`（逐 project `git status`）：只遍历 project.list 内的项目，
    vendor/lechao 不在列表中 → 永不进入扫描
  * `_scan_extra_aosp_non_repo`（顶层目录遍历）：`top_projects` 因 project.list 含
    `vendor/brcm` 而含 `vendor`，`if bn in top_projects: continue` 直接跳过整个
    `vendor/` 顶层目录（非 `is_excluded_dir` 排除——排除正则仅
    `out|prebuilts|.git|__pycache__`）
  * 后果：workspace 中 vendor/lechao 的未归档文件（如 `tests/`、`aidl_api/` 版本目录）
    不会被 EXTRA 扫描发现，只能人工归档（2026-08-26 已手动补归档 lcview tests/ 5 文件）

## 不做的事（YAGNI）

- 不自动 `git add`/`git commit`
- 不处理 `code/others/`（仅 kernel/aosp，`SRC-003`）
- 不做反向 patch（用 `git checkout` 更可靠）
- 不做多平台（仅 rpi5，未来扩展另立）


## 退出码
| 退出码 | 含义 | 下一步 |
|--------|------|--------|
| 0 | 成功 | 正常继续 |
| 1 | 脚本逻辑错误 | 检查日志 |
| 3 | 环境缺失 | 安装依赖后重试 |
| 4 | 无需同步（plan 为空） | 正常，无需操作 |

## TODO 跟踪
- [x] Step 1: 生成同步计划
- [x] Step 2: AI 主持逐条确认
- [x] Step 3: 最终确认 + 执行
- [x] Step 4: 落盘校验
- [x] Step 5: 执行结果报告
- [x] Step 6: 后续（不自动）

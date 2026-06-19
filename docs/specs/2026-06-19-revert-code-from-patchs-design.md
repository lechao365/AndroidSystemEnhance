# revert-code-from-patchs 工作流设计

> **日期**：2026-06-19
> **状态**：已确认，待实施
> **范围**：新增 `revert-code-from-patchs` 工作流，以 `patchs/rpi5` 为已知良好基线，把 `~/workspace` 中偏离 patchs 的部分拉回一致

---

## 1. 背景与动机

### 1.1 现状

`sync-code-to-patchs` 工作流实现了 `workspace → patchs/rpi5` 的全量镜像归档：workspace 改动验证 OK 后，通过脚本把定制改动（modified 存 `.diff`、new 存完整文件）同步到 `patchs/rpi5/`，确保 patchs 是 workspace 定制改动的**精确镜像**。

项目架构约束（[source-code-modify.md](../../engineering/harness/rules/source-code-modify.md)）：
- `~/workspace/` 是编译源码树（唯一参与编译）
- `patchs/rpi5/` 是单向归档目录，也是系统增强能力的**真相源**（后续可适配 RK3588 等其他平台，脱离特定 workspace 独立存在）

### 1.2 问题

最近一次 workspace 改动过大，导致树莓派无法启动，长时间定位无果。现需要把 workspace 回退到 patchs 记录的"上次已知良好"状态。

现有工具链缺少**逆操作**：没有 `patchs → workspace` 的回退能力。

### 1.3 目标

新增 `revert-code-from-patchs` 工作流：
- **方向**：`patchs/rpi5 → workspace`（与 sync 对仗）
- **语义**：patchs 是真相源，把 workspace 中偏离 patchs 的部分拉回一致
- **可靠性第一**：因修改 workspace 编译源码树，必须逐条人工确认后再执行，防止回退不干净导致刷机后依然 NG

---

## 2. 与 sync-code-to-patchs 的对仗关系

| 维度 | sync-code-to-patchs | revert-code-from-patchs |
|------|---------------------|--------------------------|
| 方向 | workspace → patchs（归档） | patchs → workspace（回退） |
| 触发时机 | workspace 改动验证 OK 后 | workspace 改坏、需回到 patchs 基线 |
| 数据流向 | workspace 是真相 | patchs 是真相 |
| 修改对象 | patchs（单向归档目录） | workspace（编译源码树） |
| 风险等级 | 低（只改归档） | 高（改编译源码，影响刷机） |
| 确认粒度 | 无（脚本自动镜像） | 逐条人工确认 |

---

## 3. 核心语义

> `patchs/rpi5` 是 workspace 定制改动的**已知良好基线**。本工作流把 workspace 中偏离 patchs 的部分**拉回与 patchs 一致**，用于在 workspace 改坏后回到上次归档的可工作状态。

### 前置约束

1. 操作对象仅限 `~/workspace/`（kernel + aosp），**不动 `patchs/`**
2. 执行前建议用户 `git stash`/commit 保存当前坏状态现场，便于事后定位根因（脚本不自动备份）
3. **不自动 `git add`/`git commit`**：执行后 working tree 处于回退后状态，由用户决定是否提交（便于 `git diff` 复查）

### 与 source-code-modify.md 的关系

本工作流是 source-code-modify.md "workspace 是源头"原则的**受控例外**：
- 日常流程：workspace 是源头，改动验证后归档到 patchs
- 回退场景：workspace 处于不可用的坏状态、需要回到 patchs 基线时，允许反向把 patchs 状态写回 workspace

这不改变日常归档流程，仅作为灾难恢复手段。

---

## 4. 四类改动分类与动作矩阵

脚本扫描 workspace 与 patchs 的差异后，所有差异归入 5 类。**MATCH 类（MODIFIED-MATCH / NEW-MATCH）不列入 plan**（仅汇总数量），其余 3 类逐条列出供确认。

| 类别 | 含义 | 检测方法 | 可选动作 | 是否列入 plan |
|------|------|---------|---------|--------------|
| **MODIFIED-MATCH** | patchs 有 modified 记录，workspace 当前 = patchs 描述 | 归一化比较 workspace 当前 diff 与 patchs `.diff` | `[skip]`（默认，OK 不动） | ❌ 仅汇总数量 |
| **NEW-MATCH** | patchs 有 new 记录，workspace 存在且内容 = patchs | 逐字节比较 workspace 文件 vs patchs 文件 | `[skip]`（默认，OK 不动） | ❌ 仅汇总数量 |
| **MODIFIED-DIVERGED** | patchs 有 modified 记录，workspace 当前 ≠ patchs（被二次改过） | 同 MODIFIED-MATCH 检测，diff 不一致 | `[checkout]` checkout upstream + 按 patchs 重新 patch（默认）<br>`[skip]` 保留现状<br>`[checkout-only]` 仅 checkout upstream（移除该定制） | ✅ 逐条 |
| **NEW-MISMATCH** | patchs 有 new 记录，workspace 缺失或内容 ≠ patchs | 同 NEW-MATCH 检测，缺失或不一致 | `[restore]` 从 patchs 复制回 workspace（默认）<br>`[skip]` 不动 | ✅ 逐条 |
| **EXTRA** | workspace 有改动但 patchs 未覆盖（未归档调试代码 / 坏改动） | workspace 改动集合 − patchs 覆盖集合 | `[revert]`（默认）<br>`[skip]` 保留<br>`[stash-hint]` 提示用户 `git stash` | ✅ 逐条 |

### EXTRA 细分与 `revert` 动作映射（便于判断坏改动来源 + 明确操作）

| EXTRA 细分 | 含义 | `revert` 的具体操作 |
|-----------|------|-------------------|
| **EXTRA-MODIFIED** | workspace 修改了 upstream 文件，但 patchs 无对应 `.diff` | `git checkout $BASE -- $f`（恢复 upstream 原样） |
| **EXTRA-NEW-TRACKED** | workspace 中 tracked 但 patchs 未记录（罕见，git 状态异常） | `git checkout $BASE -- $f`（恢复 upstream 原样，**不是 rm**） |
| **EXTRA-NEW-UNTRACKED** | workspace 中 untracked 新文件，patchs 未记录 | `rm -f $f`（物理删除） |

### 动作语义对照（与 sync 的镜像关系）

| revert 动作 | 语义 | 对应 sync 操作的反向 |
|------------|------|---------------------|
| `checkout`（modified） | git checkout $BASE → git apply patchs.diff | sync 生成 .diff 的逆 |
| `restore`（new） | cp patchs/new/... workspace | sync 复制 new 的逆（补回缺失） |
| `revert`（EXTRA-MODIFIED / EXTRA-NEW-TRACKED） | git checkout $BASE -- $f | （无对应，EXTRA 本就未归档） |
| `revert`（EXTRA-NEW-UNTRACKED） | rm $f | （无对应） |

---

## 5. plan 文件格式与两阶段执行

### 5.1 设计原则

采用 **plan/apply 两阶段解耦**：
- **plan 阶段**：扫描 + 生成结构化 plan 文件，不动 workspace
- **apply 阶段**：读已确认的 plan 文件，仅执行标记为选中的条目

理由：
1. plan 与 execute 解耦，plan 可复查、可追溯
2. 执行清单是数据文件，万一回退出问题能查证"执行了什么"
3. 与 sync-code-to-patchs 的 `--check-only`/执行 两段式风格一致
4. MATCH 类（workspace 已与 patchs 一致）不列入 plan，只汇总数量，避免干扰决策

### 5.2 plan 文件格式

采用 **TSV**（制表符分隔，可读、可 grep、可编辑），默认输出到 `/tmp/`：

```
# REVERT-PLAN generated at 2026-06-19T12:00:00
# 格式: <标记>\t<类别>\t<项目>\t<相对路径>\t<动作>\t<差异摘要>
# 标记: + = 选中执行, - = 不执行(默认)
# 类别: MODIFIED-DIVERGED | NEW-MISMATCH | EXTRA-MODIFIED | EXTRA-NEW-TRACKED | EXTRA-NEW-UNTRACKED
# 动作: checkout | checkout-only | restore | revert | skip | stash-hint

+	MODIFIED-DIVERGED	kernel	drivers/usb/storage/transport.c	checkout	workspace 比 patchs 多 15 行（疑似坏改动）
-	MODIFIED-DIVERGED	aosp:device/brcm/rpi5	device.mk	skip	仅注释差异，保留现状
+	NEW-MISMATCH	kernel	vendor/lechao/LcView/lcview_main.c	restore	workspace 缺失，从 patchs 恢复
+	EXTRA-MODIFIED	kernel	drivers/input/mouse/elog.c	revert	workspace 修改了 upstream 文件但未归档
+	EXTRA-NEW-UNTRACKED	aosp:vendor/lechao	vendor/lechao/debug_temp.c	revert	未归档调试文件
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| 标记 | `+` 选中执行 / `-` 不执行。默认值见各类别动作矩阵 |
| 类别 | 差异分类（见第 4 节） |
| 项目 | `kernel` / `aosp` / `aosp:<repo_project>`（需精确定位 repo 才能正确 checkout） |
| 相对路径 | 相对 workspace 项目根的路径 |
| 动作 | 执行动作（用户可改） |
| 差异摘要 | 行数差 / 缺失状态，供判断 |

### 5.3 两阶段命令

**阶段 1：生成 plan（默认）**
```bash
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --plan-file /custom/path.tsv
```
输出：plan 文件路径 + 汇总表（各类数量、各类默认动作分布）。**不动 workspace**。

**阶段 2：执行 plan**
```bash
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --apply --plan-file /tmp/revert-plan-xxx.tsv
```
脚本：
- 读 plan，**只执行标记为 `+` 的条目**
- 每条执行前打印动作（`[CHECKOUT] kernel:drivers/usb/storage/transport.c`）
- 失败时：打印错误、**立即停止**、退出码非 0（避免半完成状态）
- 全部成功后：输出执行汇总（各类执行数量）

**仅扫描预览（不生成 plan 文件）**
```bash
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --check-only
```

### 5.4 参数清单

| 参数 | 说明 |
|------|------|
| 无参数 | 生成 plan 到 `/tmp/revert-plan-<timestamp>.tsv` |
| `--plan-file <path>` | 指定 plan 文件路径（生成或读取） |
| `--apply` | 执行模式，**必须**配合 `--plan-file` |
| `--check-only` | 仅扫描输出差异汇总，不生成 plan 文件 |
| `-h` / `--help` | 用法说明 |

### 5.5 AI 主持的逐条确认流程

1. AI 调阶段 1 → 读取生成的 plan 文件 → 在对话中分组展示给用户
2. 用户逐条/逐类指示（"这条 skip""这组全选"等）
3. AI **直接编辑 plan 文件**的 `+`/`-` 标记
4. 用户最终确认 → AI 调阶段 2（`--apply`）执行
5. 执行后 AI 报告结果（各类执行数量、失败列表）
6. **落盘校验（强制，全量）**：apply 完成后，脚本用与阶段 1 相同的扫描逻辑重跑一遍，生成校验 plan，与原 plan 对比，分 4 类输出：
   - ✅ `FIXED`：原 plan 中 `+` 执行的条目，现已是 MATCH（回退生效）
   - ⚠ `KEPT`：原 plan 中 `-` skip 的条目，仍偏离（用户主动保留，**不算失败**）
   - ❌ `RESIDUAL`：原 plan 中 `+` 执行的条目，apply 后仍偏离（**回退未生效，真正失败**）
   - ❓ `NEW-DIFF`：apply 后新出现的差异（理论上不应有，需排查）

   落盘文件：`/tmp/revert-verify-<timestamp>.tsv`
   退出码：有 `RESIDUAL` 或 `NEW-DIFF` → 非 0（失败）；仅 `KEPT` → 0（成功）

---

## 6. 异常场景处理

可靠性第一，脚本必须覆盖以下边界：

| 场景 | 检测 | 处理 |
|------|------|------|
| workspace 不存在 | `-d $KERNEL_WS/.git` 和 `$AOSP_WS/.repo` 都失败 | 报错退出，提示检查 `KERNEL_WS`/`AOSP_WS` 环境变量 |
| 无法确定 upstream base | `find_upstream_base` 返回空 | 报错退出，提示 `git remote -v` 检查上游；**不 fallback 到 HEAD** |
| patchs 目录无 modified/new | `find patchs/rpi5/{kernel,aosp}` 为空 | 报错退出，提示"patchs 为空，无基线可回退" |
| patchs 的 `.diff` 损坏 | `git apply --check` 失败 | 标记 `BROKEN-DIFF`，该条 plan 动作降级为 `[skip]` + 警告，**不 checkout**（避免回退后无法重新 patch 导致定制丢失） |
| checkout 时文件有未暂存改动 | `git checkout -- $f` 前 `git diff --quiet` 失败 | 正常（就是要丢弃这些改动），plan 里已标 `checkout`，执行即可 |
| `git apply`（重新 patch）失败 | checkout 后 apply 失败 | **立即停止**，报错"patchs 的 diff 无法应用到 upstream"，提示该 diff 可能与 upstream base 不匹配 |
| EXTRA 命中编译产物 | 命中 `EXCLUDE_RE`/`EXCLUDE_DIR_RE` | 直接 SKIP，不列入 EXTRA（复用 sync 的排除规则） |
| AOSP 非 repo 目录的 EXTRA-NEW | 非 repo 目录的未归档文件 | 列入 plan，动作 `revert` = `rm`（需用户确认） |
| plan 中动作非法 | apply 阶段解析动作不在白名单 | 跳过该条 + 警告，继续其他 |
| plan 文件不存在 | apply 阶段 `--plan-file` 指向不存在文件 | 报错退出 |
| `--apply` 但未指定 `--plan-file` | 参数缺失 | 报错退出（apply 必须有明确 plan） |
| workspace 有 staged 改动 | apply 前 `git diff --cached --quiet` 非 0 | **仅警告不阻断**，提示"有 staged 改动，checkout 可能影响 index" |
| apply 后仍有 RESIDUAL（执行过但未生效） | 落盘校验阶段对比发现原 plan `+` 条目仍偏离 | 列出残留条目，提示可能原因（staged 改动覆盖、diff 与 upstream base 不匹配），退出码非 0 |
| apply 后出现 NEW-DIFF（新差异） | 落盘校验发现原 plan 外的新偏离 | 列出新差异，提示可能原因（apply 副作用），退出码非 0 |

### 幂等性保障

- **同一 plan 可重复 apply**：`checkout` 幂等（多次 checkout 结果一致）；`cp`（restore）幂等（覆盖）；`rm`（revert EXTRA）幂等（不存在则跳过，不报错）
- **失败可重试**：单条失败不影响其他，修正问题后重新 `--apply` 同一 plan，已完成条目自动跳过

### 安全兜底

- `--apply` 模式开始前，AI 在对话层展示 plan 路径、选中条目数、各类动作数量，等用户最终 `y` 确认
- 脚本不在 `--apply` 模式自动跳过 plan 生成阶段（必须显式传 plan 文件）
- 全程 `set -uo pipefail`，关键命令失败即停

---

## 7. 脚本结构与复用

### 7.1 文件组织

```
engineering/harness/workflows/revert-code-from-patchs/
├── revert_code_from_patchs.sh   # 主脚本（plan + apply 两阶段）
└── WORKFLOW.md                  # 工作流文档
.opencode/commands/revert-code-from-patchs.md  # 命令入口（@WORKFLOW.md）
```

### 7.2 复用 sync-code-to-patchs 的逻辑

| 复用项 | 说明 |
|--------|------|
| `REPO_ROOT` 定位（AGENTS.md 锚点） | 原样复用 |
| `KERNEL_WS` / `AOSP_WS` 环境变量 | 原样复用 |
| `EXCLUDE_RE` / `EXCLUDE_DIR_RE` 排除规则 | 原样复用（编译产物不列入 EXTRA） |
| `find_upstream_base()` | 原样复用（保证回退的 upstream 与 sync 的 diff upstream 是同一 commit） |
| `_discover_non_repo()` | 原样复用（AOSP 非 repo 目录发现） |
| 颜色/log helpers | 原样复用 |
| repo 项目扫描（`project.list` + xargs 并行） | 原样复用 |

### 7.3 新增函数

```
revert_code_from_patchs.sh
├── gen_plan()                 # 阶段1：扫描 + 生成 plan
│   ├── scan_kernel_modified() # 比对 patchs .diff vs workspace 当前 diff
│   ├── scan_kernel_new()      # 比对 patchs new vs workspace 文件
│   ├── scan_aosp_modified()   # 遍历改动项目，逐项目比对
│   ├── scan_aosp_new()        # 含 repo 项目 + 非 repo 目录
│   └── scan_extra()           # workspace 改动 − patchs 覆盖集合
├── apply_plan()               # 阶段2：读 plan + 执行
│   ├── do_checkout_patch()    # git checkout $BASE -- $f && git apply patchs.diff
│   ├── do_restore()           # cp patchs/new/... workspace
│   └── do_revert_extra()      # modified: checkout upstream; new: rm
└── validate_plan()            # 动作合法性、文件存在性检查
```

### 7.4 关键实现细节

**modified 一致性比较（归一化 diff）**：
```bash
# 比较两个 diff 是否语义一致（忽略 index 行的 hash，随 commit 变）
diff_normalized() {
    local d1="$1" d2="$2"
    diff <(grep -vE '^index ' "$d1") <(grep -vE '^index ' "$d2") >/dev/null
}
```

**patchs 覆盖集合构建（判定 EXTRA）**：
```bash
# 从 patchs 构建"已覆盖的 workspace 路径"集合
build_patch_coverage() {
    # kernel/modified/*.diff → 去后缀的源路径
    find patchs/rpi5/kernel/modified -name '*.diff' | sed 's|.*/kernel/modified/||;s|\.diff$||'
    # kernel/new/** → 相对路径
    find patchs/rpi5/kernel/new -type f | sed 's|.*/kernel/new/||'
    # aosp 同理，modified 含项目名前缀
}
```

**EXTRA 扫描（workspace 改动 − patchs 覆盖）**：
```bash
scan_extra() {
    local ws_changes=$(git diff $BASE --name-only; git ls-files --others --exclude-standard)
    local patch_cov=$(build_patch_coverage)
    comm -23 <(echo "$ws_changes" | sort) <(echo "$patch_cov" | sort)
}
```

**执行 checkout + 重新 patch（DIVERGED 默认动作）**：
```bash
do_checkout_patch() {
    local proj="$1" rel="$2" diff_file="$3"
    git checkout "$BASE" -- "$rel" || return 1
    git apply --check "$diff_file" 2>/dev/null || { echo "BROKEN-DIFF"; return 1; }
    git apply "$diff_file" || return 1
}
```

---

## 8. 命令入口与 WORKFLOW.md

### 8.1 命令入口（.opencode/commands/revert-code-from-patchs.md）

与现有命令风格一致：
```markdown
---
description: 生成回退计划，AI 主持逐条确认后把 workspace 拉回与 patchs/rpi5 一致
---
生成回退计划（参数透传）：
!`bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh $ARGUMENTS`

严格遵循完整工作流（计划生成 → AI 主持逐条确认 → 执行）：
@engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md
```

### 8.2 WORKFLOW.md 大纲

```
---
name: revert-code-from-patchs
description: patchs/rpi5 为基线，把 workspace 偏离部分拉回一致（计划生成 → AI 逐条确认 → 执行）。
---

# revert-code-from-patchs

## 核心语义
[第 3 节内容]

## 前置约束
[第 3 节内容]

## 工作流（5 步）
### 1. 生成回退计划（脚本）
### 2. AI 主持逐条确认
### 3. 最终确认 + 执行
### 4. 执行结果报告
### 5. 后续（不自动）

## 四类改动与动作矩阵
[第 4 节矩阵表]

## plan 文件格式
[第 5 节 TSV 格式说明]

## 异常处理
[第 6 节矩阵表]

## 不做的事（YAGNI）
- 不自动 git add/commit
- 不处理 patchs/others（仅 kernel/aosp）
- 不做反向 patch（用 checkout 更可靠）
- 不做多平台（仅 rpi5，未来扩展另立）
```

---

## 9. 不做的事（YAGNI）

| 不做项 | 理由 |
|--------|------|
| 自动 `git add`/`git commit` | 保留 working tree 状态便于 `git diff` 复查；是否提交由用户决定 |
| 处理 `patchs/others/` | others 是独立程序，无 workspace 映射，不在回退范围 |
| 反向 patch（`patch -R`） | 可能因 workspace 当前内容与 diff 上下文不匹配而失败；`git checkout` 更可靠 |
| 多平台支持（rpi-zero2w 等） | 当前需求仅 rpi5；未来扩展时新增 `--platform` 参数 |
| workspace 自动备份 | 由用户自行 `git stash`/commit（脚本无法判断哪些改动值得保留） |
| 处理 patchs 中存在但 workspace 不存在的 modified（恢复 modified） | 超出"回退"语义（那是 sync 的职责）；NEW-MISMATCH 的 `restore` 已覆盖"补回缺失"需求 |

---

## 10. 验收标准

1. `bash .../revert_code_from_patchs.sh` 能正确扫描 workspace 与 patchs 差异，生成结构化 plan 文件
2. 五类分类正确：MODIFIED-MATCH / NEW-MATCH 仅汇总，其余 3 类（DIVERGED/NEW-MISMATCH/EXTRA）逐条列入 plan
3. `--apply --plan-file X` 能正确执行 plan 中 `+` 标记的条目
4. 异常场景按第 6 节矩阵处理（重点验证 BROKEN-DIFF、apply 失败立即停止）
5. 幂等性：同一 plan 重复 apply 不报错
6. `--check-only` 能快速预览差异
7. 命令入口 `/revert-code-from-patchs` 可用，AI 能主持逐条确认流程
8. apply 成功后自动全量重跑落盘校验，生成 `/tmp/revert-verify-*.tsv`，按 `FIXED`/`KEPT`/`RESIDUAL`/`NEW-DIFF` 四类输出；有 `RESIDUAL` 或 `NEW-DIFF` 则退出码非 0

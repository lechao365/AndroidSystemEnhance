# sync-modify-to-main-base 重构：基线发布编排器（publish-main-base）设计

- 日期：2026-08-30
- 状态：已确认（2026-08-30 用户逐项评审确认，决策项见 §10）
- 范围：`harness/skills/sync-modify-to-main-base/` 重命名为 `harness/skills/publish-main-base/`，
  由"dev→main 同步末步"重构为"端到端基线发布编排器"（串联 harness 自检 → loop 上板验证 →
  修复收敛 → 文档同步 → candidate 登记 → promote）；同步更新全仓引用面与测试。

## 1. 背景与目标

### 1.1 现状问题（调查实证）

| # | 问题 | 证据 |
|---|------|------|
| P1 | promote 门禁强依赖"恰好存在覆盖 HEAD 的 verify 收据"（最近内容提交父 == verified_commit），验证是**人肉前置步骤**，漏一步即卡或产生未验证 promote | `sync_modify_to_main_base.sh:118-120` 前置校验；当前 dev 上 46 个未验证提交（verified_commit=d44d4da） |
| P2 | skill 只做"最后一步"，无自检、无验证、无文档同步编排，名称 `sync-modify-to-main-base` 描述的是"同步动作"，实际输出是"基线"（登记+文档+squash+重建 dev） | SKILL.md 工作流仅 4 步 |
| P3 | "失败即修复"的收敛循环分散在 loop-engineering 与 emit 复盘，基线侧无统一失败处理 | loop SKILL L17-19 |
| P4 | 建立基线前的 harness 自身质量门（pytest / check_skill_refs / check-docs）无制度化入口 | git-works-push SKILL L35-37 仅在 push 前跑 |

### 1.2 目标

把"dev 任务完成 → main 基线建立"固化为**单一入口编排器**：

1. 一键串联：harness 自检 → loop 上板验证（依赖 loop，同 cross-device-apply）→ 修复收敛 →
   文档同步（sync-code-to-doc）→ candidate 登记（prepare）→ promote 到 main
2. 任一环节 fail 先尝试修复（loop 收敛），无法修复则**终止并禁止 promote**（不破坏基线）
3. 人工门分层：无修改路径自动基线；需修改路径人工确认方案 + 二次确认
4. 保留现有全部安全机制：前置校验、known-issues 门禁、rollback_promote、文档提交防夹带

### 1.3 借鉴来源

- cross-device-apply 的 loop 拉起模式（`-sv → /loop-engineering 模式 A`，SKILL L64-72）
- loop-engineering 的"脚本管机械、AI 管语义"划分与退出协议（patience/total）
- 项目 spec 先例：`docs/superpowers/specs/2026-08-30-ws-loop-design.md`

## 2. 命名

| 项 | 旧 | 新（推荐） |
|----|----|-----------|
| skill 名 | `sync-modify-to-main-base` | `publish-main-base` |
| 命令 | `/sync-modify-to-main-base` | `/publish-main-base` |
| 目录 | `harness/skills/sync-modify-to-main-base/` | `harness/skills/publish-main-base/` |
| 主脚本 | `sync_modify_to_main_base.sh` | `publish_main_base.sh` |
| 登记脚本 | `baseline_register.py`（保留，路径计算 parents[2]=harness 不变） | 不变 |
| 日志目录 | `harness/log/sync-modify-to-main-base/` | `harness/log/publish-main-base/` |

> 备选：`establish-main-base` / `release-baseline` / `create-main-base`。推荐 `publish-main-base`：
> publish 突出"发布到主干"，main-base 沿用既有概念（revert-modify-from-main-base 语义可对齐）。
> **名称决策在评审时最终确认**。

## 3. 架构：编排器角色（SKILL 引导，脚本管机械门禁）

沿用项目三层正交哲学（apply/loop/verify），新 skill 定位为**基线发布编排层**：

```
publish-main-base（编排器，AI 按 SKILL 步骤执行）
  ├── 阶段0 harness 自检        → pytest + check_skill_refs + check-docs（复用现成工具）
  ├── 阶段1 前置校验（快/慢分流） → publish_main_base.sh --check（现有 check-only 逻辑）
  ├── 阶段2 上板验证（慢路径）    → /loop-engineering 模式 B（--target dev --case）
  │      收敛 pass → 收据入库（/git-works-push）→ 回阶段1
  │      收敛 fail → 终止，禁止 promote，输出诊断
  ├── 阶段3 candidate 登记       → publish_main_base.sh --prepare（现有）
  ├── 阶段4 人工评审门（修改路径强制，无修改自动）
  ├── 阶段5 文档同步            → /sync-code-to-doc --base origin/main（现有）
  └── 阶段6 promote             → publish_main_base.sh --promote（现有，含 rollback）
```

**关键原则：不复制 loop 的修复逻辑。** 修复收敛完全委托 loop-engineering（模式 B），
publish-main-base 只定义"何时进入/何时退出、失败如何分流"，避免与 loop 职责重叠（对应评审风险1）。

## 4. 工作流定义（新 SKILL.md）

入口（命令薄包装，同 `.opencode/command/cross-device-apply.md` 范式）：

```
/publish-main-base [--task <id>] [--case <标签>] [--confirm]
```

| 参数 | 说明 |
|------|------|
| `--task <id>` | known-issues 门禁任务（现有 prepare/promote 共用，promote 强制） |
| `--case <标签>` | 验证用例标签（默认 `lcview-liveness`，经 loop 模式 B 传入） |
| `--confirm` | 无修改路径也强制人工确认（默认自动基线） |

### 阶段 0：harness 自检（机械，AI 执行）

改动涉及 `harness/skills/` 或 `.opencode/command/` 时执行（参照 git-works-push L35-37 纪律）：

```bash
python3 -m pytest harness/skills/publish-main-base/tests/ -q          # 本 skill 单测
python3 -m pytest harness/skills/<本批改动的其他 skill>/tests/ -q     # 关联 skill 单测
python3 harness/lib/check_skill_refs.py                               # 引用完整性
python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --check-docs  # 文档索引一致
```

- 任一失败：属 harness 代码缺陷，**禁止进入 promote**（framework 级问题，不归 loop 修复轮）。
- 修复路径：AI 修复 harness 代码后重跑自检（此环节不耗 loop 轮次，与 apply 编辑自愈同语义）。

### 阶段 1：前置校验分流

```bash
bash harness/skills/publish-main-base/publish_main_base.sh --check [--task <id>]
```

- **通过**（收据 pass/skip 且最近内容提交父 == verified_commit）→ 快路径，跳到阶段 3。
- **失败且失败原因为"存在未验证改动"** → 慢路径，进入阶段 2。
- **失败为其他**（无收据 / 收据 result=fail / known-issues 门禁）→ 终止，禁止 promote，
  输出原因与下一步建议（此分支不得自动修复）。

### 阶段 2：上板验证（慢路径，经 loop 模式 B）

```bash
python3 harness/skills/loop-engineering/ws_session.py start --goal "基线发布验证" --target dev --case <标签>
```

按 loop SKILL 执行收敛循环（AI 语义层）：
- `run` → 按 workspace-verify SKILL 步骤 1-6 执行单次验证（同步/编译/推送/单测/验收/收据）
- `done` 记账（收据落盘后）
- **pass 轮** → 收据入库：`/git-works-push` → 回阶段 1（应通过，进阶段 3）
- **fail 轮** → AI 读收据失败现场分析 → 形成**修改方案**（改动点+预期）→ **停下等用户确认**
  → 确认后修复编辑 code/（复用 apply 机械护栏：改 .diff 跑 cdp_validate_patch.py、
  改 code/rpi5 跑 gen_manifest.py）→ 重跑 `run`/`done`
- **终结 fail**（`task_unsolvable` / `cost_cap_exceeded` / `env_fail` / `framework_error`）
  → 终止，**禁止 promote**，输出诊断报告（loop `diagnose` 产物），交 emit/人工决策

> **与 apply 的区别（对应评审风险2）**：apply 是"零确认"批次代理；publish-main-base
> 的验证失败修复涉及业务代码改动，**必须人工确认修改方案**——这正是"需要修改 → 人工介入"的语义。

### 阶段 3：candidate 登记

```bash
bash harness/skills/publish-main-base/publish_main_base.sh --prepare [--task <id>]
```

（现有逻辑不变：登记 candidate 随 dev 提交推送。）

### 阶段 4：人工评审门

| 路径 | 门 | 说明 |
|------|----|----|
| 无修改（阶段1 快路径 / 阶段2 首轮即 pass） | 自动（`--confirm` 可强制确认） | 上板验证全 pass，风险由用例资产承担（评审已认可） |
| 需修改（阶段2 发生过修复编辑） | 强确认 | 用户对 code 改动负责，二次确认改动内容后再 promote |

### 阶段 5：文档同步（promote 前）

```bash
/sync-code-to-doc --base origin/main
```

（现有 SKILL 方案确认门保留：动作清单级方案确认后落盘；文档改动以「文档(」前缀
commit + push 到 dev，仅动 `docs/**`。）

### 阶段 6：promote

```bash
bash harness/skills/publish-main-base/publish_main_base.sh --promote \
  --baseline-id <id> --message-file <f> --task <id> [--approved-by <id>]
```

（现有逻辑不变：squash 到 main + 重建 dev + 晋升 promoted；失败 rollback_promote。）

### 阶段 7：完成报告

输出：baseline_id / main 新 sha / dev 重建状态 / 收据路径 / 文档同步摘要 / 本次是否含修复。

## 5. 脚本改动清单

### 5.1 `publish_main_base.sh`（由 sync_modify_to_main_base.sh 更名）

- 全部现有行为保留（`--check-only` / `--prepare` / `--promote` / 前置校验 / known-issues 门禁 /
  提交分类回溯 / rollback_promote / 重建 dev）
- 新增 `--check` 别名（= `--check-only`，语义对齐"阶段 1 分流检查"）
- 前置校验失败时输出**失败原因分类**（machine-readable 一行，供 AI 分流）：
  - `NEED_VERIFY`：存在未验证改动（PARENT != verified_commit）→ 慢路径
  - `NO_RECEIPT` / `RECEIPT_FAIL` / `KI_BLOCKED` / `PARAM` → 终止
  - 实现：现有 stderr 文案映射到分类标签，不改变既有退出码与测试断言
- 日志目录改为 `harness/log/publish-main-base/`（gitignore 同步）

### 5.2 `baseline_register.py`

- **零逻辑改动**。目录层级不变（parents[2]=harness），仅随目录更名移动。

### 5.3 不新增编排脚本

编排由 SKILL 引导 AI 执行（与 cross-device-apply / loop-engineering 同构），
避免引入"巨型总控脚本"。阶段间状态靠 git 状态 + 收据链 + baseline-status.yaml 天然承载。

## 6. 引用面更新清单（全量）

| 文件 | 改动 |
|------|------|
| `.opencode/command/sync-modify-to-main-base.md` | `git mv` → `publish-main-base.md`，内容引用新 SKILL 路径 |
| `AGENTS.md:20,67` | 命令表 + Baseline 使用指引引用更新 |
| `harness/README.md:26,52,83` | 目录结构 + 命令表 + 状态模型引用更新 |
| `harness/rules/source-code-modify.md:26,49` | SRC-001 引用更新 |
| `harness/skills/git-works-push/SKILL.md` + `git_works_push.sh:74` | 提示语更新（`/publish-main-base --prepare`） |
| `harness/skills/sync-code-to-doc/SKILL.md:21` | 触发条件引用更新 |
| `README.md:28` | 项目 README 引用更新 |
| `harness/config/baseline-evidence-template.yaml:10` | 注释引用更新 |
| `harness/skills/sync-modify-to-main-base/tests/*` | 目录随迁 + `SCRIPT` 路径更新 + 新增用例 |
| `harness/log/`（gitignore） | 日志目录名同步 |
| `data/verify-results/*.md` 历史文本 | **不改**（历史事实，保留原名引用） |

> 改名红线：`git mv` 保留历史；SKILL 内引用的旧命令路径逐一替换；改后必须
> `python3 harness/lib/check_skill_refs.py` 无 `[MISS]`（AGENTS.md 强制）。

## 7. 测试改动

### 7.1 现有测试迁移

- `tests/test_sync_modify_integration.py` → `tests/test_publish_integration.py`
  （`SCRIPT` / 目录路径更新，其余断言不变——前置校验/回溯/门禁/rollback 行为等价）
- `tests/test_sync_modify_to_main_base.py` → `tests/test_publish_main_base.py`
  （mock 不变，路径更新）
- `tests/test_baseline_register.py` 不动（不依赖脚本名）

### 7.2 新增用例

1. `test_check_outputs_need_verify_class`：mock 收据 verified_commit 与 HEAD 父不一致 →
   `--check` 输出 `NEED_VERIFY` 分类行（退出码仍 1）
2. `test_check_outputs_ki_blocked_class`：known-issues 门禁失败 → 输出 `KI_BLOCKED` 分类行
3. `test_promote_paths_unchanged`：`--promote` 全流程回归（沿用现有集成测试，验证改名后行为等价）

### 7.3 验证命令

```bash
python3 -m pytest harness/skills/publish-main-base/tests/ -q
python3 harness/lib/check_skill_refs.py
```

## 8. 行为契约变更汇总（对比现状）

| 行为 | 现状 | 新 |
|------|------|----|
| 入口 | `/sync-modify-to-main-base`（prepare/promote 两步 + 手动文档同步） | `/publish-main-base`（一键编排，含自检/验证/文档同步） |
| 验证 | 人工前置 | 内置（经 loop 模式 B），未验证改动自动进入验证路径 |
| 失败处理 | 校验失败即 exit，人工决定 | 验证失败进入 loop 修复收敛；无法修复禁止 promote |
| 人工门 | prepare→promote 之间（隐含） | 分层：无修改自动 / 需修改强确认 |
| 文档同步 | 手动 | 阶段 5 内嵌（保留方案确认门） |
| harness 自检 | 无制度化入口 | 阶段 0（涉及 harness 改动时） |
| 脚本接口 | `--check-only`/`--prepare`/`--promote` | 新增 `--check` 别名 + 失败分类输出；既有接口不变 |

## 9. 风险与边界

| 风险 | 缓解 |
|------|------|
| 改名破坏既有引用 | 全量清单（§6）+ check_skill_refs 门禁 + 集成测试回归 |
| 编排器职责膨胀 | 只编排不实现：修复委托 loop、验证委托 workspace-verify、文档委托 sync-code-to-doc |
| 自动基线风险（用例不全/假绿） | 阶段 0 自检 + known-issues 门禁 + `--confirm` 逃生口；用例质量问题归用例资产层（评审已认可） |
| 文档同步时序 | 阶段 5 在 prepare 后、promote 前（现有 sync-code-to-doc 时序约束不变） |
| 慢路径无限循环 | loop patience/total 上限天然承接（task_unsolvable / cost_cap_exceeded 终止） |
| 设备限制 | 全部环节仅 apply 设备运行（同现状） |

## 10. 决策项（2026-08-30 已确认，全部采纳推荐）

| # | 决策项 | 结论 |
|---|--------|------|
| 1 | 命名 | **`publish-main-base`**（目录/命令/脚本同步改名） |
| 2 | 编排形态 | **SKILL 引导 AI 编排**（不新增总控脚本） |
| 3 | 无修改路径 | **默认自动基线**，`--confirm` 逃生口强制人工确认 |
| 4 | 阶段 0 harness 自检 | **纳入**，涉及 harness 改动时执行 |
| 5 | 阶段 5 文档同步 | **内嵌**，保留 sync-code-to-doc 方案确认门 |
| 6 | `--case` 默认值 | **`lcview-liveness`**（与 workspace-verify 模式 B 默认对齐） |

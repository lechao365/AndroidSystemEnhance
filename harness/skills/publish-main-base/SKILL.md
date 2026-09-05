---
name: publish-main-base
description: 一键基线发布编排器：harness 自检 → loop 上板验证（未验证改动自动进入）→ 修复收敛 → 文档同步 → candidate 登记 → promote 到 main；任一环节无法修复则禁止 promote。
no_commit: true
stages:
  - research: "阶段0-1 自检 + 前置校验分流"
  - plan: "阶段2 验证路径（loop）与人工确认修改方案"
  - code: "阶段3-6 prepare / 评审门 / 文档同步 / promote"
  - review: "阶段7 完成报告与基线核对"
---
# publish-main-base

> **仅限 apply 设备（本地 WSL2）运行**。

核心语义：dev 任务完成后一键建立 main 基线。编排器只定义"何时进入/退出/失败如何分流"，
**修复收敛完全委托 loop-engineering，验证执行委托 workspace-verify，文档委托
sync-code-to-doc**——不复制任何子 skill 的实现逻辑。
任一环节 fail 先尝试修复（loop 收敛），无法修复则**终止并禁止 promote**（不破坏基线）。
## Trigger（触发条件）
- emit 侧宣告任务结束，准备建立新基线（一键入口，替代原手动 prepare→doc→promote 三步）
## 入口参数（AI 编排层语义，非 publish_main_base.sh 参数）
- `--task <id>`：known-issues 门禁任务（透传给 `--check`/`--prepare`/`--promote`）
- `--case <标签>`：验证用例标签（支持逗号分隔多用例），传 loop 模式 B（默认 `lcview-liveness,lcview-transfer,lcview-pipeline,lcview-perf`）
- `--confirm`：无修改路径也强制人工确认（默认自动基线）
## Preconditions（前置条件）
- 当前分支 dev；工作树干净；origin 可达
- 最新收据 pass/skip 且最近内容提交父 == verified_commit（快路径）；
  否则自动进入验证路径（阶段 2）
- known-issues 门禁：promote 不强制 `--task`（门禁无条件执行，缺省由
  check-issues 推断唯一活跃任务，推断失败即拒）；目标任务下存在
  origin=introduced 或 blocking 且 status!=fixed 的问题即拒
- package 硬门禁（批次 ff33f92060ac 方向 3）：dev 相对 origin/main 动过 code/
  且 candidate `package_result` 非 PASS 即阻断晋升——PASS 仅由 ws_package
  打包证据（script_rc=0）产生；`evidence_scope=no-code-change`（无代码改动）
  豁免不受限。打包证据生产：python3 harness/skills/workspace-verify/ws_package.py
  （mode 0 仅打包，前置校验三镜像齐备，BLD-007 sudo 显式传
  TARGET_PRODUCT/ANDROID_PRODUCT_OUT，自描述证据落
  harness/log/workspace-verify/package-<batch_id>.json）
## Human confirmation gates（人工确认门）
- 阶段 2 验证 fail 轮的**修改方案须人工确认**（涉及业务代码改动，用户对改动负责）；
  确认后修复 → 重验 pass 后**二次确认**才进 promote
- 阶段 5 文档同步沿用 sync-code-to-doc 的动作清单级方案确认门
- 无修改路径默认自动基线（`--confirm` 可强制人工确认）
- 高危动作（整卡刷写/boot dd）沿用 workspace-verify 内部确认门
## Outputs / artifacts（输出/产物）
- 设计文档同步改动（随 dev squash 进入 main）
- main 新 squash commit（含代码+文档+收据+baseline 登记）；dev 重建指向 main
- data/verify-results 新收据（验证路径产出）+ harness/log/publish-main-base/ 运行日志
- 阶段 7 完成报告：baseline_id / main sha / dev 状态 / 收据 / 文档摘要 / 是否含修复
## Failure / recovery（失败/恢复）
- 阶段 0 自检失败：harness 代码缺陷，修复后重跑（不耗 loop 轮次）
- 阶段 2 loop 终结 fail（task_unsolvable / cost_cap_exceeded / env_fail /
  framework_error）：终止并禁止 promote，输出诊断报告，交 emit/人工决策
- promote 中 push/merge/squash 失败：脚本已跑 rollback_promote（main 本地 reset 回
  origin/main 丢弃 squash commit、dev 回退 HEAD^ 并 revert-candidate），人工核对后重试
- dev 重建失败：main 已含基线，仅需人工 `git checkout dev && git reset --hard main
  && git push -f origin dev`（勿重跑 promote）
- 文档同步遗漏：promote 已把代码 commit 进 main，`git diff HEAD` 无变动；
  只能等下批次 promote 前补（勿在 promote 后硬同步）
## Related policy IDs（关联规则 ID）
- SRC-004（code dev/main 为恢复真相源，不要求 promoted）
- CDP-001（提交分类约定：登记元「构建(baseline):」/ 文档「文档(」/ 内容提交）
---
## 工作流（7 阶段）

### 阶段 0：harness 自检（涉及 harness 改动时执行）
本批改动涉及 `harness/skills/` 或 `.opencode/command/` 时，先跑（任一失败即 harness
代码缺陷，**禁止进入 promote**，修复后重跑；不耗 loop 轮次）：
```bash
python3 -m pytest harness/skills/publish-main-base/tests/ -q
python3 -m pytest harness/skills/<本批改动的其他 skill>/tests/ -q
python3 harness/lib/check_skill_refs.py
python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --check-docs
```

### 阶段 1：前置校验分流
```bash
bash harness/skills/publish-main-base/publish_main_base.sh --check [--task <id>]
```
- 通过（exit 0）→ 快路径，跳阶段 3
- `check_class=NEED_VERIFY`（存在未验证改动）→ 慢路径，进阶段 2
- 其他 `check_class`（NO_RECEIPT / RECEIPT_FAIL / DOC_VIOLATION / KI_BLOCKED）→
  终止，**禁止 promote**，按分类处理（补验证/修文档提交/处理 known-issues）

### 阶段 2：上板验证（慢路径，经 loop 模式 B）
```bash
python3 harness/skills/loop-engineering/ws_session.py start --goal "基线发布验证" --target dev --case <标签>
```
（`--case` 默认 `lcview-liveness,lcview-transfer,lcview-pipeline,lcview-perf`，与 workspace-verify 模式 B 对齐）
按 loop SKILL 执行收敛循环（AI 语义层）：
- `run` → 按 workspace-verify SKILL 步骤 1-6 执行单次验证（同步/编译/推送/单测/验收/收据）
- 收据落盘后 `done` 记账
- **pass 轮** → `/git-works-push`（收据入库）→ 回阶段 1（应通过，进阶段 3）
- **fail 轮** → AI 读收据失败现场分析 → 形成**修改方案**（改动点+预期）→
  **停下等用户确认** → 确认后修复编辑 code/（复用 apply 机械护栏：改 .diff 跑
  cdp_validate_patch.py、改 code/rpi5 跑 gen_manifest.py）→ 重跑 `run`/`done`
- **终结 fail**（task_unsolvable / cost_cap_exceeded / env_fail / framework_error）→
  终止，**禁止 promote**，输出诊断（loop diagnose 产物），交 emit/人工决策

> 与 apply 的差异：apply 是零确认批次代理；本阶段验证失败修复涉及业务代码改动，
> **必须人工确认修改方案**，重验 pass 后**二次确认**才进 promote（用户对 code 改动负责）。

### 阶段 3：candidate 登记
```bash
bash harness/skills/publish-main-base/publish_main_base.sh --prepare [--task <id>] [--evidence-scope <scope>]
（--evidence-scope 缺省从最新 board 收据 cases 推导；人工传值须为其子集，防过度声称）
```
（登记 candidate 随 dev 提交推送；输出 baseline_id）

### 阶段 4：人工评审门
| 路径 | 门 |
|------|----|
| 无修改（快路径 / 验证首轮即 pass） | 自动（`--confirm` 可强制人工确认） |
| 需修改（阶段 2 发生过修复编辑） | 强确认（用户二次确认改动内容） |

### 阶段 5：文档同步（promote 前，内嵌）
```bash
/sync-code-to-doc --base origin/main
```
（沿用 sync-code-to-doc 动作清单级方案确认门；文档改动以「文档(」前缀 commit + push
到 dev 且仅动 `docs/**`——promote 脚本会校验防夹带）

### 阶段 6：promote
```bash
bash harness/skills/publish-main-base/publish_main_base.sh --promote \
  --baseline-id <id> --message-file <f> [--task <id>] [--approved-by <id>]
```
（squash 会把阶段 5 文档改动一并并入 main；--approved-by 缺省为 lechao；
失败自动 rollback_promote）

### 阶段 7：完成报告
输出：baseline_id / main 新 sha / dev 重建状态 / 收据路径 / 文档同步摘要 / 是否含修复
（核对 origin/main == origin/dev == 本地 dev）
## 退出码（publish_main_base.sh）
0 成功（含 check-only 干跑）/ 1 校验失败（前置校验、fetch 与登记提交失败；promote 中途
merge/squash 失败已 rollback 回退 dev）/ 2 push 类失败 / 3 参数错误
/ 4 dev 无领先提交

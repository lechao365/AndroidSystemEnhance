---
name: sync-modify-to-main-base
description: dev 验证 OK 后 promote 到 main 生成基线（candidate 自动登记 → 人工评审 → promoted）。
no_commit: true
stages:
  - research: "前置校验（收据 pass/skip + HEAD^ 判定）"
  - plan: "prepare 登记 candidate"
  - code: "promote squash + 重建 dev"
  - review: "拉起文档同步"
---
# sync-modify-to-main-base

> **仅限 apply 设备（本地 WSL2）运行**。

核心语义：dev 最新收据 pass/skip 且无未验证改动时，prepare 登记 candidate →
人工评审 → promote（squash 到 main + 重建 dev + 晋升 promoted）→ AI 拉起文档同步。
## Trigger（触发条件）
- emit 侧宣告任务结束，dev 最新收据 pass/skip，准备生成新基线
## Preconditions（前置条件）
- 最新收据 result∈{pass,skip}（-s 批次 skip 视为 OK）且 HEAD^ == verified_commit
  （dev 无未验证改动；revert/fail 收据拒绝）；dev 领先 main ≥1 提交
## Human confirmation gates（人工确认门）
- prepare 与 promote 之间的人工评审（检查 candidate 记录与 dev 内容）
## Outputs / artifacts（输出/产物）
- main 新 squash commit（含代码+收据+baseline 登记）；dev 重建指向 main
- harness/log/sync-modify-to-main-base/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- push main 或 merge/squash 失败（exit 1/2）：脚本已跑 rollback_promote
  回退 dev 到 HEAD^ 并 revert-candidate，人工须核对 dev 与登记后重试
- dev 重建失败：delete 失败自动 +dev 强推；再失败 exit 2 转人工
## Related policy IDs（关联规则 ID）
- SRC-004（promoted 才是恢复真相源）
---
## 工作流
1. 前置校验 + prepare：
   bash harness/skills/sync-modify-to-main-base/sync_modify_to_main_base.sh --prepare
   （输出登记的 baseline_id；candidate 随 dev 提交推送）
2. 人工评审：检查 baseline-status.yaml candidate 记录（收据路径可点开核对）
3. AI 生成 squash message 后 promote：
   bash .../sync_modify_to_main_base.sh --promote --baseline-id <id> --message-file <f>
4. **promote 成功后 AI 必须立即执行 /sync-code-to-doc 工作流**（文档同步入闭环，
   脚本无法自动拉起 opencode 命令，由 AI 在会话内触发）
## 退出码
0 成功（含 check-only 干跑）/ 1 校验失败（前置校验、fetch 与登记提交失败；promote 中途 merge/squash 失败已 rollback 回退 dev）/ 2 push 类失败 / 3 参数错误 / 4 dev 无领先提交
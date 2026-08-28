---
name: git-works-push
description: 收集工作树 diff → AI 生成中文 commit message → commit 并 push origin dev（收据随批入库）。
no_commit: false
stages:
  - research: "收集 diff"
  - plan: "AI 生成 commit message"
  - code: "commit + push"
  - review: "核对远端 sha"
---
# git-works-push

> **仅限 apply 设备（本地 WSL2）运行**。

自包含精简版：脚本做机械工作（diff 收集、git add/commit/push dev），AI 做语义工作（理解 diff、生成 message）。零确认：AI 生成 message 后直接调脚本，无需人工确认。
## Trigger（触发条件）
- cross-device-apply 编辑完成后（verify 收据已落盘）
- 人工单独提交 dev 改动
## Preconditions（前置条件）
- 当前分支 dev；工作树有改动（normal 模式）；收据文件 data/verify/ 已就位（随批入库）
## Human confirmation gates（人工确认门）
- 零确认
## Outputs / artifacts（输出/产物）
- origin/dev 新 commit（代码 + 收据同批）
- harness/log/git-works-push/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- push 失败（exit 2）：commit 保留，转人工处理（pull --rebase 后 --push-only）
- 无改动（exit 4）：提示无需推送
---
## 工作流
1. 收集 diff：git status --porcelain + git diff HEAD --stat
   （大 diff 降级：>50 文件或 >5000 行时每文件只取前 20 行，仅用于生成 message）
2. AI 生成中文 commit message（docs/commit-message-format.md，六种 type）
3. 预览确认链路（可选）：bash harness/skills/git-works-push/git_works_push.sh --dry-run
4. 执行：bash harness/skills/git-works-push/git_works_push.sh --message-file <临时文件>
5. 核对：git ls-remote origin dev == 本地 HEAD（不等于则报错转人工）
## 退出码
0 成功 / 1 守卫失败 / 2 push 失败（commit 保留）/ 3 参数错误 / 4 无改动
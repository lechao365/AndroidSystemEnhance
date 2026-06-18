---
description: 收集 diff → AI 生成中文 type commit message → 单次确认（支持多轮编辑）→ 提交推送
---
收集 diff（无参数）：
!`bash engineering/harness/workflows/git-push-to-server/collect_diff.sh`

AI 根据 $ARGUMENTS 解析参数（--branch/--remote/--no-push/--dry-run），按工作流处理：
@engineering/harness/workflows/git-push-to-server/WORKFLOW.md

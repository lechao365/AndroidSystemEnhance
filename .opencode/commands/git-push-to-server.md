---
description: 收集 diff → AI 生成中文 type commit message → 单次确认（支持多轮编辑）→ 提交推送
---
收集 diff（参数透传）：
!`bash skills/git-push-to-server/collect_diff.sh $ARGUMENTS`

严格遵循完整工作流（生成 message → 单次确认 → commit + push）：
@skills/git-push-to-server/SKILL.md

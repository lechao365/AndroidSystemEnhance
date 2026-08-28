---
description: "[DEPRECATED] 旧流程归档命令，新流程 code/dev 为源头，仅历史场景保留"
---
执行归档脚本（参数透传：默认归档，--check-only 仅检查）：
!`python3 harness/skills/sync-workspace-to-code/sync_workspace_to_code.py $ARGUMENTS`

严格遵循完整工作流（归档结果判定 + README 一键自动更新）：
@harness/skills/sync-workspace-to-code/SKILL.md

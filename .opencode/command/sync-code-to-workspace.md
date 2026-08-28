---
description: 生成同步计划，AI 主持逐条确认后把 workspace 拉回与 code/rpi5 一致
---
生成同步计划（参数透传）：
!`python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py $ARGUMENTS`

严格遵循完整工作流（计划生成 → AI 主持逐条确认 → 执行 → 落盘校验）：
@harness/skills/sync-code-to-workspace/SKILL.md

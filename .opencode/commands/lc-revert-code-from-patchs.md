---
description: 生成回退计划，AI 主持逐条确认后把 workspace 拉回与 patchs/rpi5 一致
---
生成回退计划（参数透传）：
!`bash engineering/harness/workflows/lc-revert-code-from-patchs/revert_code_from_patchs.sh $ARGUMENTS`

严格遵循完整工作流（计划生成 → AI 主持逐条确认 → 执行 → 落盘校验）：
@engineering/harness/workflows/lc-revert-code-from-patchs/WORKFLOW.md

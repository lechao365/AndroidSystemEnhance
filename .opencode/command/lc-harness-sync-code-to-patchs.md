---
description: 全量镜像 workspace→code/rpi5（含删除对齐）并自动更新 README 文件映射表
---
执行归档脚本（参数透传：默认归档，--check-only 仅检查）：
!`python3 harness/skills/lc-harness-sync-code-to-patchs/lc_harness_sync_code_to_patchs.py $ARGUMENTS`

严格遵循完整工作流（归档结果判定 + README 一键自动更新）：
@harness/skills/lc-harness-sync-code-to-patchs/SKILL.md

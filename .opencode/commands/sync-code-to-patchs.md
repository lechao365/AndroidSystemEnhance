---
description: 全量镜像 workspace→patchs/rpi5（含删除对齐）并自动更新 README 文件映射表
---
执行归档脚本（参数透传：默认归档，--check-only 仅检查）：
!`bash skills/sync-code-to-patchs/sync_code_to_patchs.sh $ARGUMENTS`

严格遵循完整工作流（归档结果判定 + README 一键自动更新）：
@skills/sync-code-to-patchs/SKILL.md
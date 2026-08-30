---
description: 按 diff 将 code 变动精准转换为技术文档更新（取上下文→定位章节→动作清单→确认落盘→自检）
---
生成变动报告（参数透传）：
!`python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py $ARGUMENTS`

常用形态：
- 工作区未提交变动（apply 后、commit 前）：不带参数
- dev 相对 main 批次变动（promote 前）：`--base origin/main`
- 完整 diff 正文（AI 零往返）：追加 `--full-diff`；仅检查不输出提示：追加 `--check-only`

严格遵循完整工作流（方案先行，确认后落盘）：
@harness/skills/sync-code-to-doc/SKILL.md

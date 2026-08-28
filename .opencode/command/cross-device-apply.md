---
description: 解析 CDP 批次并编辑 code/dev（-sv 拉起验证，完成后推送 dev，仅 apply 设备）
---
严格遵循完整工作流（precheck 含 base 拒批 → 编辑载体规则 → verify 分流 → push）：
@harness/skills/cross-device/cross-device-apply/SKILL.md
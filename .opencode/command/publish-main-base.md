---
description: 一键基线发布：harness 自检 → loop 上板验证（未验证改动自动进入）→ 修复收敛 → 文档同步 → candidate 登记 → promote 到 main；无法修复则禁止 promote（仅 apply 设备）
---
严格遵循完整工作流（阶段0 harness 自检 → 阶段1 前置校验分流 --check → 阶段2 验证路径 /loop-engineering 模式B → 阶段3 prepare 登记 → 阶段4 人工评审门 → 阶段5 /sync-code-to-doc --base origin/main → 阶段6 promote → 阶段7 完成报告）：
@harness/skills/publish-main-base/SKILL.md

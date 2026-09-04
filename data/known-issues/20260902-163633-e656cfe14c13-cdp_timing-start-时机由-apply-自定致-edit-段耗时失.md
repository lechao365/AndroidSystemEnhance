- schema_version: 1
- issue_id: KI-20260902-001
- title: cdp_timing start 时机由 apply 自定致 edit 段耗时失真
- discovered_in: c06a99f89a09
- origin: pre-existing
- severity: P1
- blocking: False
- blocking_reason: 
- status: fixed
- task: cdp-timing-start
- resolved_in: 42accb5

## body

e656cfe14c13（-s 批）edit 段仅 0.181s：cdp_timing start 由 apply 在 precheck 前自定触发，precheck 通过后随即 mark edit，两 mark 间隔反映"打点动作间隔"而非真实编辑耗时。
apply 侧实际编辑（源码 + 单测改动）耗时显著大于 0.181s，打点语义失真，emit 据此定位耗时瓶颈会误判 edit 环节极快。
修法方向：编辑环节按真实活动区间打点（start 与编辑起点解耦，或编辑完成点再 mark，使 edit 段覆盖真实编辑耗时）。

## 闭环记录（2026-09-04，KIR-006）

修法落地：selfcheck 开跑前自动判定编辑是否收口，未收口补打 mark edit
（同名自动 #N）——编辑区间口径不再依赖 AI 自判，loop 轮修复编辑同契约
入账（C3 联动：编辑开始前 mark edit_plan，完成由 selfcheck 收口为
edit#N）。闭环后 edit 段跨批可比（-s 批漂移形态由补打归口）。
按 KIR-006 终态不写时老化，待 promote 清算删除。

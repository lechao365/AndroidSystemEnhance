- schema_version: 1
- issue_id: KI-20260903-002
- title: baseline_register promote 未传审批人时回落默认常量并自动填时间，审批可自证
- discovered_in: 5b06712c1138
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: open
- task: baseline-promote-approval
- resolved_in: 

## body

baseline_register.py promote 分支 `approved_by = args.approved_by or "lechao"`：未传 --approved-by
时回落默认常量 "lechao" 并自动填 approved_at 当前时间——晋升记录显示审批人是默认值而非实际操作者，
审批环节可自证（无法区分人工审批与默认回落）。
KIR 判定：KIR-001 回退本批改动（baseline_register/publish_main_base/baseline-status）后
回落行为仍存在（297 行历史既有逻辑，非本批引入）；KIR-002 不阻塞（promote 流程正常，
仅审批溯源语义失真）；KIR-003 不在开工冻结清单。
修法方向：promote 缺 --approved-by 时拒绝或要求显式人工确认（不回落默认常量），
或把回落值改为显式"unknown-approver"并附提示，保证审批人字段可溯源。

- schema_version: 1
- issue_id: KI-20260903-001
- title: selfcheck 每批可能跑多次致收据 timings 同名 apply_selfcheck 重复出现
- discovered_in: 074b07d3592d
- origin: pre-existing
- severity: P1
- blocking: False
- blocking_reason: 
- status: open
- task: ws-report-timings-dedup
- resolved_in: 

## body

-s 批收据 timings 字段同名 apply_selfcheck 段重复出现（如 6e380b831fc4 批出现两次 19~22s 段）：
selfcheck.py 每次运行即自发 mark apply_selfcheck，ws_report 流程为采集证据会再次执行 selfcheck，
两次打点同名段，ws_report 写入收据 timings 时按原始 mark 序列透传、不合并不去重，
emit 按名归因耗时产生歧义。
KIR 判定：KIR-001 回退本批改动（cdp_parse/ws_acceptance/cdp-contract）后重复现象仍存在
（selfcheck 自发打点 + ws_report 复跑采集是历史既有行为，非本批引入）；KIR-002 不阻塞
（自检双 rc 0 正常，重复仅打点诊断归因歧义，重跑结果一致）；KIR-003 不在开工冻结清单。
修法方向：ws_report 写收据 timings 前对同名段合并/去重（如同名取末个或求和并标注次数），
或 selfcheck 支持 --no-mark 由 ws_report 复跑采集时不重复打点。

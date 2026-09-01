- schema_version: 1
- issue_id: KI-20260831-001
- title: precheck 领先超一笔无告警
- discovered_in: ab303f32bdaf
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: open
- task: lcview-refactor
- resolved_in: 

## body

cdp_parse/emit_precheck 对 dev 领先 origin/main 超过一笔内容提交无任何告警——批量连续 apply 后 HEAD 与 verified_commit 之间的中间提交全部无验证证据，precheck 只校验 base 匹配，不校验领先笔数。建议：领先>1 笔时 emit 侧 WARN，提示先 /publish-main-base 再继续产批。

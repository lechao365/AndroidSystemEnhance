- schema_version: 1
- issue_id: KI-20260831-001
- title: precheck 领先超一笔无告警
- discovered_in: ab303f32bdaf
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: fixed
- task: lcview-refactor
- resolved_in: 41daf105ac74

## body

cdp_parse/emit_precheck 对 dev 领先 origin/main 超过一笔内容提交无任何告警——批量连续 apply 后 HEAD 与 verified_commit 之间的中间提交全部无验证证据，precheck 只校验 base 匹配，不校验领先笔数。建议：领先>1 笔时 emit 侧 WARN，提示先 /publish-main-base 再继续产批。

## 修复记录
- 修复批次: cdp_emit_precheck 增 lead_warns——origin/main..origin/dev 提交数>1 即产
  告警串合入 warns 输出（提示先 /publish-main-base），main 缺失返空不崩。
- 闭环证据: lead_warns 实测产告警（领先 >1 笔时输出
  "dev 领先 main N 笔内容提交（中间提交无验证证据）..."），resolved_in=本批 batch_id。

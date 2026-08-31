- schema_version: 1
- issue_id: KI-20260831-002
- title: publish-main-base SKILL 阶段三漏写 evidence-scope
- discovered_in: ab303f32bdaf
- origin: introduced
- blocking: False
- blocking_reason: 
- status: fixed
- task: lcview-refactor
- resolved_in: 01b665fa99dc

## body

SKILL.md 阶段 3 --prepare 命令示例未含 --evidence-scope（上批新增必填参数），按示例执行必退 3。文档与实现漂移——SKILL L97 命令缺参，应补 --evidence-scope 或注明缺省从收据 cases 推导（本批已改为推导优先）。

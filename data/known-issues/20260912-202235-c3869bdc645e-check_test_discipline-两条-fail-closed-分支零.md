- schema_version: 1
- issue_id: KI-20260912-004
- title: check_test_discipline 两条 fail-closed 分支零守护，可无声退化 fail-open
- discovered_in: 61927c927825
- origin: pre-existing
- severity: P1
- blocking: False
- blocking_reason: 
- status: open
- task: checker-hardening
- resolved_in: 
- archived_in: 
- kind: 

## body

- 方向4: check_test_discipline 两条 fail-closed 分支零守护，可无声退化 fail-open。
- 定位: harness/lib/check_test_discipline.py:110-129 scan() 三处 git 失败 fail-closed 分支——tracked（:111-114）、deleted（:119-122）、untracked（:125-129），任一返 None 均输出哨兵违规判红（discipline_rc=1）。
- 证据: 三处 fail-closed 分支中仅 tracked 一条有测试守护（test_check_test_discipline.py:236-248 TestDisciplineFailClosed.test_git_failure_reported_red mock _git_lines 返 None）；deleted（:118-122）与 untracked（:123-129）两条 fail-closed 分支零用例覆盖。若未来实现被误改为 git 失败时静默返回 []（当"无删除/无未跟踪"假绿放行），无测试拦截，守卫可无声退化 fail-open（删测试换绿 / 未跟踪违禁行漏检）。
- 影响: fail-closed 语义（lib-07）只在 tracked 分支被固化，deleted/untracked 两路守卫退化为 fail-open 时不可见。
- 修法方向: 补 deleted/untracked 两条 _git_lines 失败判红用例（与 tracked 对齐）。

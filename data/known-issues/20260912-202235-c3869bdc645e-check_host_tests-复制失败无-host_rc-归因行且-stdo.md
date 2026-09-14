- schema_version: 1
- issue_id: KI-20260912-006
- title: check_host_tests 复制失败无 host_rc 归因行且 stdout 空
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

- 方向6: check_host_tests 复制失败无 host_rc 归因行且 stdout 空。
- 定位: harness/lib/check_host_tests.py:69（stage = _stage_module_copy(repo, module) 在 try 块 :71 之前）；:54-56（rmtree/copytree 未包异常）。
- 证据: _run_make_test 中 make 前的 _stage_module_copy（rmtree/copytree）若抛 OSError（权限/磁盘满/并发互踩 FileExistsError，见 KI-20260912-001），异常直接冒泡到 main()，stdout 无任何 host_rc= 行（对照 make 缺失:77 / 超时:79 / rc 非零:89 均有 "host_rc=1 | error: ..." 归因行），仅 stderr traceback。selfcheck 侧 host_out 为空字符串、host_rc 靠进程 returncode 判红——判红成立但归因丢失，故障现场不可见。
- 影响: 副本复制阶段故障静默无归因，host 收口红时无法区分复制失败与 make 失败，排障靠猜。
- 修法方向: _stage_module_copy 异常捕获并返回 "host_rc=1 | error: 副本复制失败（<module>）" 归因行，与 make 失败口径对齐。

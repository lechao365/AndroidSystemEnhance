- schema_version: 1
- issue_id: KI-20260912-002
- title: _stage_module_copy 只复制 tests 子目录时 8 用例仍全绿（全 mock subprocess.run，无断言副本含父目录源文件）
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

- 方向2: _stage_module_copy 只复制 tests 子目录时 8 用例仍全绿（全 mock subprocess.run，无断言副本含父目录源文件）。
- 定位: harness/lib/tests/test_check_host_tests.py（8 用例：test_run_one_make_test_success/fail、test_clean_always_runs_after_test、test_make_dir_missing_returns_one、test_make_runs_in_staged_copy_not_source、test_main_red_when_make_missing/timeout/rc_nonzero）；harness/lib/check_host_tests.py:48-50 注释声称"拷贝整个 vendor/lechao（非单模块），须含顶层 kernel_lechao_log.h"。
- 证据: 8 个用例全部 mock.patch.object(cht.subprocess, "run")，从不真实执行 copytree/make；test_make_runs_in_staged_copy_not_source 只断言 make 的 cwd 落在副本 tests 目录、以及收尾副本被回收，无任何断言副本内容含父目录源文件（如 kernel_lechao_log.h 存在）。若把 _stage_module_copy 改成只复制 <module>/tests 子目录（破坏 -I../.. 编译依赖 kernel_lechao_log.h 的前提），8 用例仍全绿——副本内容正确性零守护，注释承诺的隔离语义无测试固化。
- 影响: 副本内容回归（如未来改为单模块/仅 tests 复制）不会被测试发现，host 单测在缺失顶层头时到真机上板才暴露编译失败。
- 修法方向: 补真实 copytree 后断言副本含顶层 kernel_lechao_log.h（或改用临时目录真实执行一次 make）的用例。

- schema_version: 1
- issue_id: KI-20260831-003
- title: emit 侧 35 项 bash 测试平台跳过成盲区
- discovered_in: ab303f32bdaf
- origin: pre-existing
- severity: P1
- blocking: False
- blocking_reason: 
- status: fixed
- task: lcview-refactor
- resolved_in: 41daf105ac74

## body

test_git_works_push / test_publish_* 等依赖 bash 脚本的测试在 Windows(emit) 侧经 pytest 平台跳过（pytest.skip bash 不可用），35 项从未在 emit 侧执行——apply 侧 WSL 虽跑，但 emit 侧改动后无本地回归护栏。建议：WSL 侧 CI 或 emit 侧脚本转 Python 实现（如 lc_skills_commit_and_push.sh 的 bash -n 语法预检）。

## 修复记录
- 修复批次: 打开 emit 侧 bash 测试盲区（shell_env.find_bash/bash_argv + python3 shim，
  经 git 路径推 Git for Windows bash.exe 并绕开 PATH 强插失效）→ 修复批次后两轮收敛
  （开关跨模块污染与 Windows 实跑 8 项失败）。
- 闭环证据: emit 侧 pytest 48 项全通 0 skip（此前 35 项 bash 依赖测试平台跳过成盲区）,
  resolved_in=本批 batch_id。

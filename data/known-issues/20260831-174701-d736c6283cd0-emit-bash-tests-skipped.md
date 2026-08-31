- schema_version: 1
- issue_id: KI-20260831-003
- title: emit 侧 35 项 bash 测试平台跳过成盲区
- discovered_in: ab303f32bdaf
- origin: pre-existing
- blocking: False
- blocking_reason: 
- status: open
- task: lcview-refactor
- resolved_in: 

## body

test_git_works_push / test_publish_* 等依赖 bash 脚本的测试在 Windows(emit) 侧经 pytest 平台跳过（pytest.skip bash 不可用），35 项从未在 emit 侧执行——apply 侧 WSL 虽跑，但 emit 侧改动后无本地回归护栏。建议：WSL 侧 CI 或 emit 侧脚本转 Python 实现（如 lc_skills_commit_and_push.sh 的 bash -n 语法预检）。

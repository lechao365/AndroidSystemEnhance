- schema_version: 1
- issue_id: KI-FLAKE-c3869bdc645e-377de5
- title: [flake] harness/skills/git-works-push/tests/test_git_works_push.py::TestGitWorksPush::test_no_receipt_code_staged_escape_env_warns
- discovered_in: c3869bdc645e
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: open
- task: auto-flake
- resolved_in: 
- archived_in: 
- kind: flake

## body

- nodeid: harness/skills/git-works-push/tests/test_git_works_push.py::TestGitWorksPush::test_no_receipt_code_staged_escape_env_warns
- round: 1
- first_seen_batch: c3869bdc645e
- rerun_cmd: python3 -m pytest harness/skills/git-works-push/tests/test_git_works_push.py::TestGitWorksPush::test_no_receipt_code_staged_escape_env_warns -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

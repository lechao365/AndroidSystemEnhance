- schema_version: 1
- issue_id: KI-FLAKE-d6daed24110b-6b8d79
- title: [flake] harness/skills/cross-device/tests/test_cdp_issue.py::TestBackfilledSeverity::test_all_repo_issues_pass_validation
- discovered_in: d6daed24110b
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: fixed
- task: auto-flake
- resolved_in: 20260912-494cb5191b53
- archived_in: 
- kind: flake

## body

- nodeid: harness/skills/cross-device/tests/test_cdp_issue.py::TestBackfilledSeverity::test_all_repo_issues_pass_validation
- round: 4
- first_seen_batch: d6daed24110b
- rerun_cmd: python3 -m pytest harness/skills/cross-device/tests/test_cdp_issue.py::TestBackfilledSeverity::test_all_repo_issues_pass_validation -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

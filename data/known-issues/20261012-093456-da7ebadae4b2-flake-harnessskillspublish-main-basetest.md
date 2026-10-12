- schema_version: 1
- issue_id: KI-FLAKE-da7ebadae4b2-ed391a
- title: [flake] harness/skills/publish-main-base/tests/test_baseline_register.py::TestBaselineRegister::test_add_candidate_explicit_id_duplicate_rejected
- discovered_in: da7ebadae4b2
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

- nodeid: harness/skills/publish-main-base/tests/test_baseline_register.py::TestBaselineRegister::test_add_candidate_explicit_id_duplicate_rejected
- round: 1
- first_seen_batch: da7ebadae4b2
- rerun_cmd: python3 -m pytest harness/skills/publish-main-base/tests/test_baseline_register.py::TestBaselineRegister::test_add_candidate_explicit_id_duplicate_rejected -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

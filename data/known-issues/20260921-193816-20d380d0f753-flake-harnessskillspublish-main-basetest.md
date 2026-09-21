- schema_version: 1
- issue_id: KI-FLAKE-20d380d0f753-e1789e
- title: [flake] harness/skills/publish-main-base/tests/test_publish_integration.py::TestSyncModifyIntegration::test_prepare_source_commit_and_dedup
- discovered_in: 20d380d0f753
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

- nodeid: harness/skills/publish-main-base/tests/test_publish_integration.py::TestSyncModifyIntegration::test_prepare_source_commit_and_dedup
- round: 1
- first_seen_batch: 20d380d0f753
- rerun_cmd: python3 -m pytest harness/skills/publish-main-base/tests/test_publish_integration.py::TestSyncModifyIntegration::test_prepare_source_commit_and_dedup -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

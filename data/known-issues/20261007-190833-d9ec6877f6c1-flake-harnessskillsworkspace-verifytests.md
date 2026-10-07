- schema_version: 1
- issue_id: KI-FLAKE-d9ec6877f6c1-24bc54
- title: [flake] harness/skills/workspace-verify/tests/test_lcview_check.py::TestPerfRegressionGate::test_gate_within_tolerance_ok
- discovered_in: d9ec6877f6c1
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

- nodeid: harness/skills/workspace-verify/tests/test_lcview_check.py::TestPerfRegressionGate::test_gate_within_tolerance_ok
- round: 1
- first_seen_batch: d9ec6877f6c1
- rerun_cmd: python3 -m pytest harness/skills/workspace-verify/tests/test_lcview_check.py::TestPerfRegressionGate::test_gate_within_tolerance_ok -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

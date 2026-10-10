- schema_version: 1
- issue_id: KI-FLAKE-6824b8320bf9-8ed89d
- title: [flake] harness/skills/workspace-verify/tests/test_lcview_check.py::TestPerfRegressionGate::test_gate_throughput_drop_red
- discovered_in: 6824b8320bf9
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

- nodeid: harness/skills/workspace-verify/tests/test_lcview_check.py::TestPerfRegressionGate::test_gate_throughput_drop_red
- round: 1
- first_seen_batch: 6824b8320bf9
- rerun_cmd: python3 -m pytest harness/skills/workspace-verify/tests/test_lcview_check.py::TestPerfRegressionGate::test_gate_throughput_drop_red -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

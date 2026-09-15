- schema_version: 1
- issue_id: KI-FLAKE-26cacb40dfd6-c1860c
- title: [flake] harness/skills/workspace-verify/tests/test_ws_verify_chain.py::TestChain::test_chain_ensures_timings_wired_to_report
- discovered_in: 26cacb40dfd6
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: wontfix
- task: auto-flake
- resolved_in: 
- archived_in: 
- kind: flake

## body

- nodeid: harness/skills/workspace-verify/tests/test_ws_verify_chain.py::TestChain::test_chain_ensures_timings_wired_to_report
- round: 1
- first_seen_batch: 26cacb40dfd6
- rerun_cmd: python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py::TestChain::test_chain_ensures_timings_wired_to_report -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

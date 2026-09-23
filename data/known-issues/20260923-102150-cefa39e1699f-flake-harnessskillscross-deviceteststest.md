- schema_version: 1
- issue_id: KI-FLAKE-cefa39e1699f-1bac08
- title: [flake] harness/skills/cross-device/tests/test_cdp_receipt.py::TestReceipt::test_prune_dedupe_spares_coverage_receipt
- discovered_in: cefa39e1699f
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

- nodeid: harness/skills/cross-device/tests/test_cdp_receipt.py::TestReceipt::test_prune_dedupe_spares_coverage_receipt
- round: 1
- first_seen_batch: cefa39e1699f
- rerun_cmd: python3 -m pytest harness/skills/cross-device/tests/test_cdp_receipt.py::TestReceipt::test_prune_dedupe_spares_coverage_receipt -q
- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，放行本轮；未闭环 flake 阻断 promote）

# harness 工程实践沉淀（docs/harness-design）

本目录把深度检视中确认「做得好、可复用」的工程实践整理为可复用的设计说明，
供后续开发直接照搬、评审对照。每篇文档都标注真实 `file:line` 证据，而非泛泛
而谈。硬约束已提炼进 `harness/rules/`（见文末）。

## 文档索引

| 文档 | 一句话说明 | 关联规则 |
|------|-----------|---------|
| [atomic-evidence-write.md](atomic-evidence-write.md) | 原子写原语（tmp 名带 pid+线程 id + `os.replace`）如何根除「半写证据文件」 | `HPY-002` |
| [selfcheck-parallel.md](selfcheck-parallel.md) | 自检的「两阶段 spawn/collect + pytest 期间重叠 + 逐检查器耗时归因」编排 | `CDP-DOD-001` |
| [kernel-host-dual-test.md](kernel-host-dual-test.md) | 内核纯逻辑抽离 + host 双轨单测（同一 `.c`），含 UAF 防护与先判后算防溢出 | `CXX-001`~`CXX-004` |
| [fail-closed-evidence.md](fail-closed-evidence.md) | 证据链 fail-closed 与 provenance 纪律：收据白名单/目录边界/同批绑定/原子登记 | `CDP-DOD-002`、`HPY-002` |

## 相关规则

- [harness-python-conventions.md](../../harness/rules/harness-python-conventions.md)：`HPY-001`~`HPY-003`，把上文可复用实践中的 L1/L2/L6 收敛为硬约束。

## 使用方式

- 新增 harness Python 模块或证据文件落盘逻辑前，先读 `atomic-evidence-write.md` 与 `harness-python-conventions.md`。
- 调整自检编排（并行/超时/耗时归因）前，先读 `selfcheck-parallel.md`。
- 改动内核环形缓冲区或跨内核/host 共用逻辑前，先读 `kernel-host-dual-test.md`。
- 改收据/证据链判定（覆盖/打包证据/登记）前，先读 `fail-closed-evidence.md`。

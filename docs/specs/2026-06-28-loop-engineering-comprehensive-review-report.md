# Loop Engineering 框架全面检视报告

> 文档类型：检视报告（review report）
> 检视对象：`engineering/loop/`（loop engineering 全部框架代码、业务 case 与设计文档）
> 检视日期：2026-06-28
> 检视方式：顶层设计文档建立"预期基线" + 五模块并行深度检视（controller / core / connection / deploy / cases·scripts·config）+ 对比业界 loop engineering 最佳实践识别 gap
> 测试基线：controller 208 passed / core 216 passed / connection 216 passed / deploy 全通过

---

## 1. 检视范围与方法

| 模块 | 职责 | 检视产出 |
|------|------|----------|
| `contracts/` | 数据契约（LoopSession / RuntimeState / CheckpointRecord / FailureCode） | 文档严重过时 |
| `controller/` | 状态图 runtime 引擎 + guard + checkpoint + 三层 analyzer + patch 防护 | P0×2、文档×4 |
| `core/` | 单次 attempt 验证引擎（case 加载/断言/执行/证据） | P0×1、P1×2 |
| `connection/` | 传输层（adb + rp5-serial），串口 IP 发现 | P1×2、健壮性多项 |
| `deploy/` | 编译/部署/回滚 + 四阶段防护网 | P0×3、安全 fail-open |
| `cases/scripts/config` | 声明式用例、CLI 入口、配置 | 配置过宽、文档矛盾 |

"预期基线"取自：`WORKFLOW.md`、`controller/README.md`、`contracts/README.md`、`docs/specs/2026-06-26-loop-runtime-rearchitecture-design.md`。

---

## 2. 总体评价

整个 loop 框架已是一套较成熟的 autonomous engineering loop，架构范式与业界一致：

- LangGraph 式**状态图引擎**（node + guard + checkpoint/resume）
- **三层降级 analyzer**（KnowledgeBase 0.98 → Scripted 0.95 → Opencode/LLM 0.8），成本/可靠性分层
- **Reflexion 式知识库**（成功补丁归档、指纹召回）
- **Human-in-the-loop gate**（confidence 阈值）
- **确定性断言 + EvidenceBundle**（可验证性强，优于纯 LLM agent）
- **声明式 case**（零 Python）
- **四阶段部署防护网**（物理设备 in-the-loop 的安全意识，业界 loop 罕见）

但严格检视下，**"全绿测试"掩盖了主闭环断裂**：数个致命 bug 的单测本身使用了与运行时不符的假数据（例如 KB 归档测试构造了运行时根本不存在的嵌套结构）。**当前"自动修复→自学习"主闭环因 P0-1/P0-2 实际跑不通，dd 部署链存在刷砖风险（P0-3/4/5）。**

---

## 3. P0 致命/安全缺陷（必须修）

### P0-1 KB 指纹归档与查询永不匹配 → Reflexion 闭环断裂

- **现象**：`DONE_SUCCESS` 时归档成功补丁到知识库，但归档指纹与 `KnowledgeBaseAnalyzer` 查询指纹永远无法匹配。
- **证据**：
  - `controller/python/loop_controller/runtime/engine.py:489`：归档取 `latest.get("verify", {}).get("failed_cases", [])`，而 `stages.run_verify_stage` 写入的 attempt 结构是**扁平的**（`failed_cases` / `case_results` 直接在顶层，无 `verify` 嵌套）→ 恒取空。
  - 即使修正路径，`DONE_SUCCESS` 时 latest attempt 是"全 PASS 那次"，`failed_cases` 必为空；而查询指纹由"失败那次"的非空 `failed_cases` 计算 → 空 vs 非空，永不相等。
  - 单测 `tests/test_runtime_engine.py:1158` 使用了不存在的嵌套结构 `{"verify": {"failed_cases": [...]}}`，掩盖了该 bug。
- **影响**：第一层 KnowledgeBaseAnalyzer(0.98) 永不命中，历史成功补丁无法被同类失败召回，**Reflexion 自学习完全失效**，每次同类故障都降级到 Scripted/LLM 或退人工。
- **修复方向**：归档时使用"触发本次修复的那次失败 attempt"的 `failed_cases`（即 `attempts[-2]` 或修复链路上的失败快照）计算指纹；同步修正单测使用扁平 attempt 结构；补"归档→召回"端到端集成测试。

### P0-2 approve 后无法续跑（HITL 死锁）

- **现象**：低置信/内核补丁/dd 部署触发 human gate 后，人工 `approve` 无法恢复运行。
- **证据**：
  - `engine.py:251 / 417 / 568` 等多处同时设置 `terminal_state=ESCALATE_HUMAN` 与 `pending_human_gate=True`。
  - `runtime_cli.py:318` `_handle_approve` 只清 `pending_human_gate=False`，**不清** `terminal_state`。
  - `runtime_cli.py:232` / `engine.py:69` `resume()` 首行 `if terminal_state != NONE: return` → 直接返回，无法续跑。
- **影响**：所有"terminal+gate 同时设置"的场景 approve 静默无作为（LOW_CONFIDENCE / KERNEL_PATCH_REVIEW / DD_BOOT_REVIEW 这三种纯 pending 场景可正常 approve）。
- **修复方向**：**分离语义**——`pending_human_gate` 表"暂停可续跑"，`terminal_state` 表"流程终结"，二者互斥；`approve` 时显式将 `terminal_state` 复位为 `NONE`；补死锁场景测试。

### P0-3 rollback SHA 校验逻辑恒为 False → 备份完整性架空

- **现象**：dd 回滚前的备份完整性校验完全失效。
- **证据**：`deploy/python/loop_deploy/rollback.py:66` 条件 `sha_parts[0] != sha_parts[0].strip()` 左右恒相同 → 永远为 False；且 `sha_check` 为空时整段校验被跳过，直接进入 dd。
- **影响**：损坏/不完整的备份照样被 dd 写入，回滚保护形同虚设。
- **修复方向**：与部署前记录的 `backup_sha` 做实际比对，不一致或取不到即 fail-closed 升级人工（`ROLLBACK_FAILED`）。

### P0-4 dd 部署防护网多处 fail-open

- **现象**：四阶段防护网在多个失败分支只 warning 不阻断，仍继续 dd。
- **证据**：
  - `deployer.py:183`：磁盘空间 `df` 解析失败 → warning 后继续 dd。
  - `deployer.py:202`：host 备份为空（`host_backup`/`backup_sha` 空）→ 跳过 `verify_backup_integrity`，继续 dd。
  - `deployer.py:307`：serial shell 不可达 → warning 后仍返回成功。
- **影响**：防护网设计意图被绕过，**可能刷坏设备**。
- **修复方向**：safety-critical 默认 **fail-closed**——任一阶段不确定即阻断 + 升级人工。

### P0-5 deployer root() 返回值丢弃

- **现象**：`deployer.py:127` `self._client.root(timeout_sec=10.0)` 返回值未检查，root 失败仍 push boot.img + dd。
- **影响**：adbd 非 root 运行时 dd 静默失败。
- **修复方向**：检查 root 结果，失败即中止部署。

### P0-6 required collector 抛错被静默判 PASS

- **现象**：`required: true` 的 collector 执行抛 OSError（如设备失联）时，suite 仍判 PASS。
- **证据**：`core/python/loop_core/executor.py:98-109` 降级构造 `CollectorResult` 时**未传 `status`**，默认 `status="ok"`（`models.py:88`）；`executor.py:137` 失败判定 `cr.required and cr.status != "ok"` 因此恒不成立。
- **影响**：`required` collector 的"失败即 suite FAIL"承诺被绕过，验证可靠性受损。
- **修复方向**：降级分支显式 `status="error"` 并填 `error=str(exc)`。

---

## 4. P1 功能不达预期

| # | 缺陷 | 证据 | 修复方向 |
|---|------|------|----------|
| P1-1 | serial 平面不回填退出码，`exit_code_zero/equals` 在主通道恒失败（文档宣称 9 断言可用，2 个不可用） | `connection/.../rp5_serial/transport.py:334` 不设 exit_code；`core/.../assertion_engine.py:129` | serial 命令后注入 `; echo "__rc=$?"` 解析回填；或文档明确 exit_code 断言仅限 adb/host |
| P1-2 | 参数化 `exit_code_equals` 恒失败（assert.value 无条件 `str()`，`0(int)=="0"`→False） | `core/.../case_loader.py:276` | 仅在含 `${item}` 时替换，保留非字符串原值类型 |
| P1-3 | decider 两个均可 push 的模块被误升级 FLASH_FULL，违反"能 push 不 dd" | `deploy/.../decider.py:84`（`type_count>=2`→flash_full） | 改为取最高风险等级（te > kernel/rc > cpp），而非无条件 flash_full |
| P1-4 | Writer 泄漏：client 异常断开 `_cleanup` 不释放 writer，无 TTL 回收，须重启 host | `connection/.../host/handler.py:289`；`serial_runtime.py:104`（expires_at==acquired_at） | `_cleanup` 释放 writer/关闭 session；为 WriterLease 引入 TTL 回收 |
| P1-5 | device-ip 未排除 169.254 link-local，DHCP 失败返回无效 IP 且不报错 | `scripts/rp5_serial_helper.py:52` | 排除 127./169.254./0.0.0.0；取不到有效 IP 即报错退出 |
| P1-6 | 补丁白名单可路径穿越（`startswith` 不防 `../`）+ 白名单过宽 | `controller/.../patch_guard.py:23`；`config/target-paths.yaml`（`device/brcm/rpi5/`、`vendor/brcm/` 整目录；`patchs/` 归档目录入白名单违反归档纪律） | `Path.resolve()` 规范化后判前缀、拒绝含 `..`；白名单收敛到文件/精确子目录；移除 `patchs/` 条目 |

---

## 5. P2 健壮性 / 一致性

| # | 缺陷 | 证据 |
|---|------|------|
| P2-1 | checkpoint JSONL 无坏行容错，单条坏行使 `latest()/all()` 抛 JSONDecodeError 不可用 | `controller/.../runtime/checkpoint_store.py:51` |
| P2-2 | json_field：`not_exists` 数组越界误判 False；bool 当数字（`true eq 1`→通过）；字符串 eq/ne 大小写不敏感（`OK eq ok`→通过） | `core/.../assertion_engine.py:153 / 170-194` |
| P2-3 | 6+ 处 `except Exception: pass` 静默吞错（违反 CXX-004）：KB 归档(`engine.py:512`)、KB 写回(`analyzer_protocol.py:439`)、worktree 清理(`engine.py:456`)、stash 恢复(`nodes.py:186`)、故障持久化(`runtime_cli.py:398`)、transcript 落盘(`serial_runtime.py:150`) | 多处 |
| P2-4 | adb reboot 退化为 `wait-for-device`，却报 `stage_reached=l3_verified`，三级判定名不副实 | `connection/.../adb/transport.py:148` |
| P2-5 | summary.txt 缺 `recent_line_count` 渲染；transcript 每行 open/close（高频串口性能浪费，应持久句柄+定时 flush） | `core/.../evidence.py:90`；`serial_runtime.py:150` |
| P2-6 | deploy 错误码与 contracts FailureCode 不对齐（`ROLLBACK_FAILED` 在 DeployErrorCode 不存在；`KERNEL_DEAD_NO_SHELL`↔`KERNEL_PANIC`；`BOOT_TIMEOUT_ROLLBACK`↔`BOOT_COMPLETED_NOT_REACHED`） | `deploy/.../models.py` vs `contracts/.../failure_codes.py` |
| P2-7 | on_fail collector 仅 `critical+fail` 触发，但 `triggered_collectors` 字段对所有 fail（含 warn）都填；`error` 状态用例从不触发 collector → 证据"声称触发"与"实际采集"不一致，error 用例丢诊断证据 | `core/.../executor.py:73 / 330` |
| P2-8 | BaseTransport 抽象契约声明旧 API（acquire/release/...），executor 实际用新 API（mark_output_boundary/capture_since，默认 NotImplementedError）→ 抽象声明误导 | `core/.../transport.py` |
| P2-9 | host_exec `bash -lc <command>` 直接执行 + 未设 cwd（注入面 + 复现性差；YAML 受信边界内，风险有限） | `core/.../host_exec.py:21` |
| P2-10 | `acquire_writer` 可跳过 `session.open`，mode 硬编码 `interactive` 致语义漂移 | `serial_runtime.py:92` |
| P2-11 | exit_code_zero/exit_code_equals 在全部 12 个 YAML 中零使用（僵尸断言类型）；fixture 与 live 的 reboot L3 判定不一致（同用例可 fixture PASS / live FAIL） | cases/*；`transport.py:333` vs `rp5_serial/transport.py:461` |

---

## 6. 文档与实现脱节

### 6.1 contracts/README.md 严重过时（P0 文档）

| 项 | 文档声称 | 实际实现 |
|----|----------|----------|
| dataclass 数量 | 4（StageResult/AttemptState/SessionState/TerminationDecision） | 7 + 1 enum（StageResult/AttemptState/**LoopSession**/**RuntimeState**/**CheckpointRecord**/TerminationDecision/SessionState(alias) + **RuntimeTerminalState**） |
| 导出符号 | 5 | 9 |
| 核心类型 | SessionState | 主类型为 `LoopSession`，`SessionState` 已是 deprecated alias（`models.py:96`） |
| FailureCode | 10 个 | **17 个**（缺列 EVIDENCE_FAIL / DUPLICATE_PATCH / KERNEL_DEAD_NO_SHELL / TRANSPORT_UNRECOVERABLE / ROLLBACK_FAILED / VERIFICATION_REGRESSION / VERIFICATION_STUCK） |

### 6.2 controller/README.md 状态机图与 guard 清单错误（P1 文档）

- 状态机图（`controller/README.md:77`）缺失整个 `APPLY_PATCH → COMPILE_PATCH → DEPLOY_PATCH → RUN_VERIFY` 回路与 `REVERT_PATCH → DECIDE_NEXT` 分支（实现见 `engine.py:25` `_LINEAR_NEXT`）。
- guard 表错误：`deploy_failed_but_recoverable` 文档标 `→DECIDE_NEXT`，实际 `guards.py:81` 返回 `REVERT_PATCH`；`compile_failed_but_recoverable` 标 `retry` 实为 `REVERT_PATCH`；漏列 `boot_timeout_kernel_panic`（实际 **16** guard，非 15，engine.py:322 调用）。

### 6.3 WORKFLOW.md 子命令缺失

`le runtime` 子命令列表（`WORKFLOW.md:337`）缺 `pending / approve / reject`（controller/README 已列）。

### 6.4 其他文档/实现矛盾

- network-adbd case 硬编码子网 `192.168.1.0/24` 与 SSID `HUAWEI-BE7P`（环境耦合，非单一设备 IP，但迁移即误判 FAIL）：`cases/system/network-adbd-success.yaml:66 / 76`。
- `analyzer.yaml` 缺 `confidence.opencode` 字段（OpencodeAnalyzer 0.8 硬编码在代码）。
- `patch_knowledge_base.json` 示例条目 `fingerprint_components` 为空 `{}`（归一化匹配可能未启用）。
- adb_ops 注释声称 `ctl.start` 但实际由 deployer 调 `ctl.restart`：`deploy/.../adb_ops.py:23`。

---

## 7. 与业界 Loop Engineering 的 Gap

> 参照系：LangGraph（状态图/checkpoint/interrupt）、Reflexion（episodic memory）、SWE-agent / OpenHands（trajectory 驱动）、AlphaCodium（flow/分支探索）、LangSmith·AgentOps（loop 可观测）、SWE-bench（评测基线）。

| Gap | 现状 | 业界做法 | 建议 |
|-----|------|----------|------|
| **G1 记忆闭环断裂** | KB 指纹归档/查询不匹配（P0-1） | Reflexion 记忆必须可召回 | 用"触发修复的那次失败"指纹归档（与 P0-1 同源，属方法论 bug） |
| **G2 单线性修复、无分支探索** | 一次一个补丁，失败 revert 重来；`progress_converging` 仅单调下降判定，无"回退换方向" | best-of-N / tree search / flow 多候选择优 | analyzer 产多候选补丁 + 基于 checkpoint 回退换方向 |
| **G3 analyzer 缺完整轨迹上下文** | AnalysisRequest 主要带当前 failed_cases，缺历史补丁与失败原因 | 喂完整 observation-action-feedback trajectory/scratchpad | 注入"前 N 次补丁 + 失败原因"累积上下文，事前避免重复（现仅 duplicate_patch_hash 事后兜底） |
| **G4 反馈信号粒度粗** | 仅 failed_count 收敛 | reward shaping / partial credit | 回传"哪个断言更接近通过"的细粒度信号 |
| **G5 缺 loop 级可观测与预算** | checkpoint JSONL 为流水，无聚合视图；无 token/时间/编译次数预算总账 | trace tree + 全局 budget | 聚合 trace 视图 + 全局预算闸 |
| **G6 HITL 语义重叠** | pending_human_gate 与 terminal_state 概念交叉（致 P0-2） | LangGraph interrupt 单一"暂停-恢复"语义 | 拆分两概念、互斥（与 P0-2 同源） |
| **G7 安全 loop 应 fail-closed** | dd 防护网多处 fail-open（P0-4） | safety-critical 默认拒绝 | 任一阶段不确定即阻断 + 升级人工 |
| **G8 无文档-实现一致性守护** | README/状态机图/guard 清单漂移（第 6 节） | contract-first + CI 校验文档 | 加 README↔models/guard 的元测试（守护 contracts 模型清单、guard 清单、状态机节点） |
| **G9 无成功率基线/评测** | 无 loop 修复成功率指标 | SWE-bench 式 benchmark | 建回归基线，量化 loop 有效性、支撑 analyzer 策略 A/B |

---

## 8. 优先级与推进建议

1. **第一批（恢复主闭环与设备安全）**：P0-1 ~ P0-6。其中 P0-1/P0-2 直接决定"自动修复→自学习"主闭环能否跑通；P0-3/4/5 关乎 dd 刷砖风险，应改为 fail-closed。
2. **第二批（功能达标）**：P1-1 ~ P1-6（退出码断言、参数化、decider 误升级、Writer 泄漏、device-ip、白名单收敛）。
3. **第三批（健壮性与文档同步）**：P2 全部 + 第 6 节文档对齐（contracts/README、状态机图、guard 清单、WORKFLOW 子命令）。
4. **架构增强（中长期）**：G2 分支探索、G3 轨迹上下文、G5 loop 可观测/预算、G8 文档一致性元测试、G9 评测基线。

> 改动纪律（用户确认）：后续任何 `~/workspace/` 源码或 `engineering/loop/` 实现的修复，均**严格 TDD**（先写复现失败测试——注意多个现有单测使用了错误假数据需一并修正——再改实现），并先加载 `engineering/harness/rules/source-code-modify.md`。

---

## 9. 附：测试基线

| 模块 | 测试命令 | 结果 |
|------|----------|------|
| controller | `pytest engineering/loop/controller/python/tests/` | 208 passed |
| core | `pytest engineering/loop/core/python/tests/` | 216 passed |
| connection | `pytest engineering/loop/connection/providers/{rp5-serial,adb}/python/tests/` | 216 passed |
| deploy | `pytest engineering/loop/deploy/python/tests/` | 全通过 |

> 注意：测试全绿不代表行为正确——P0-1、本报告 §4/§5 多处缺陷的现有单测使用了与运行时不符的假数据或未覆盖关键路径，修复时须连同测试假设一并修正。

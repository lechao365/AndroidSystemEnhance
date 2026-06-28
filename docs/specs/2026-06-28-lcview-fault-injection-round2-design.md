# LcView 故障注入 × Loop Engineering v2 全能力验证（第 2 轮 · 独立）设计规格

**日期：** 2026-06-28
**状态：** approved
**作者：** opencode（brainstorming 产出）
**前序：** `docs/specs/2026-06-28-lcview-fault-injection-loop-validation-design.md`（第 1 轮 F1-F5）

---

## 1. 背景与动机

第 1 轮（F1-F5）已验证 LE v2 的 KB/Scripted/Opencode 三层 analyzer、PUSH_SINGLE 部署、COMPILE_FAILED→REVERT 回滚，F6（DD_BOOT_REBOOT）按风险跳过。本轮在**全新、独立、不复用 F1-F5 注入点**的前提下，覆盖上轮未充分验证的能力维度，并把真机 DD_BOOT_REBOOT 链路补全。

### 1.1 探索定性结论

| 维度 | 现状 |
|------|------|
| 框架运行时能力 | 完整：12 node / 16 guard / 3 层 analyzer（5 条 script 规则）/ 4 deploy mode / 4 阶段 dd 防护 / checkpoint / worktree 隔离 / 双层回滚 |
| baseline 测试 | 508 passed，但**全量 `pytest engineering/loop` 有 7 个 deploy 测试 flaky**；deploy 目录单独跑 41 passed → **跨模块测试污染（缺陷#1）** |
| workspace 基线 | 干净。`vendor/lechao` 仅 1 个 baseline commit `ee38ec2`，上轮 F1-F5 故障全部回滚无残留 |
| KB | 仅 1 条 lcview 条目（hit_count=0） |
| 上轮遗留缺陷 | §5.2 dmesg 基线退化；§5.3 human gate 未端到端触发；duplicate_patch_hash 仅单测 |
| 真机 | 可用（live 上板）；具备 SD 卡物理重刷兜底 |

## 2. 目标

1. **先修框架缺陷**：消除测试污染，使全量 `pytest engineering/loop` 稳定绿（"框架可用"硬门禁）
2. **注入 6 类全新故障（N1-N6）**端到端真机验证，覆盖上轮空白维度
3. **修复上轮遗留缺陷**（dmesg 基线、human gate）
4. **过程中遇到的所有问题自行修复**；仅在高风险物理操作 / 无权限 / 不可逆决策时请用户介入
5. 产出独立 spec + report，KB 增长

### 2.1 非目标

- lcview 自身功能增强（仅注入故障）
- LE 框架架构重构（仅修缺陷）
- 复用 F1-F5 注入点

## 3. 故障注入矩阵（N1-N6）

| # | 注入点 | 故障 | 捕获 case | 验证 LE 能力 | analyzer | deploy | 风险 |
|---|--------|------|----------|-------------|----------|--------|------|
| N1 | `daemon/lechao_lcview.cpp:162` 解析循环入口 | 注入 `break; //FAULT` | E13 `daemon_read_loop_active`+E7 | **KB-miss → 新增 Scripted 规则** `_rule_lcview_parse_loop_break`(0.95) | PUSH_SINGLE | 低 |
| N2 | `config/lcview_events.json` 字段 type | `int64`→`int32`（schema↔kernel 契约破坏） | E11 invalid 暴增 | **KB-miss+Scripted-miss → Opencode LLM**(0.8) | PUSH_SINGLE | 中 |
| N3 | 双故障：N1 日志故障 + `FileWriter.cpp:109` 文件名故障 | analyzer 每轮只修日志（固定补丁 hash），filename 故障使 verify 恒 FAIL | 补丁 hash 重复 | **`duplicate_patch_hash` guard → ESCALATE** | PUSH_SINGLE | 中 |
| N4 | 2~3 个独立 verify 故障（递减修复） | 每轮修 1 个：failed N→…→0 | 多 case | **`progress_converging` 收敛态 RETRY 端到端**直到 DONE_SUCCESS | PUSH_SINGLE | 中 |
| N5 | 复用 N2 类 Opencode 故障 + 临时调 `analyzer.yaml` threshold→0.9 | confidence 0.8 < 0.9 | — | **human gate 端到端**：pending→`le runtime approve`→续跑 DONE | PUSH_SINGLE | 中 |
| N6 | `daemon/lechao_lcview.rc` 追加无效 oneshot service | `service lechao_fault_test /system/bin/nonexistent` | 新增 `lcview_no_fault_service` | **DD_BOOT_REBOOT + 四阶段防护网 + 真机 dd/reboot + serial 回滚** | DD_BOOT_REBOOT | 高 |

### 3.1 关键设计取舍

- **N3**：guard chain 中 `duplicate_patch_hash` 优先级高于 `progress_converging`。第 1 轮（attempt 1，无历史 hash）走 RETRY；第 2 轮 analyzer 产出同 hash 补丁 → 命中 `duplicate_patch_hash` → ESCALATE。双故障是自然触发该 guard 的最小构造。
- **N4**：回归态（latest>previous）难自然构造，保留单测覆盖；收敛态（latest<previous>0 → RETRY）端到端验证。
- **N6**：默认验证「部署链路 + 四阶段防护 + 一次 dd + reboot + serial 回滚」；无效 oneshot service 不触发 kernel panic（init 标记失败继续 boot），120s 内串口/adb 可达。**dd 写入前向用户做最终确认**（不可逆物理操作）。

## 4. 框架缺陷与遗留修复

| 项 | 根因 | 修复 | 验证 |
|----|------|------|------|
| 缺陷#1 测试污染 | 某前序模块测试泄漏全局 mock 未 teardown | 定位泄漏测试，改 `monkeypatch` fixture / setup-teardown 恢复 | 全量 `pytest engineering/loop` 稳定绿 |
| §5.2 dmesg 基线退化 | `lcview_kernel_module_loaded` 依赖 dmesg 环形缓冲滚出 | cases 改用 `/proc/modules` 或 `lsmod` | 长运行后稳定 PASS |
| §5.3 human gate | confidence 0.8 > 0.7 默认不触发 | 由 N5 端到端覆盖 | N5 走通 approve |

## 5. 执行策略（用户指令）

1. **风险递增顺序**：缺陷#1（纯本地 python，最低风险）→ §5.2 dmesg（yaml/case）→ N1 → N2 → N3 → N4 → N5（改 threshold）→ N6（真机 dd，最高风险，最后）
2. **自主修复**：流程中遇到的所有问题自行修复，不逐项确认
3. **介入门槛**：仅当①高风险不可逆物理操作（N6 dd 写入前）②无权限③需要用户独占资源/决策时，才暂停并给出必须介入的明确原因

### 5.1 统一闭环

```
git 干净基线 → 注入故障 → le runtime init --target lcview --suite <suite> --max-attempts 5 --artifacts-dir <新目录>
  → le runtime run --session <s> --adb-endpoint <真机IP:5555>
  → 状态机自动闭环 → 校验 terminal_state + attempt 历史 + checkpoint
  → git checkout 回滚故障（worktree 隔离自动清理）→ 下一个故障
```
真机 IP 由串口 `rp5_serial_helper.py device-ip` 动态发现，禁止硬编码。

## 6. 产出与归档

- 新 report：`docs/specs/2026-06-28-lcview-fault-injection-round2-report.md`（独立证据矩阵）
- 证据归档：`engineering/output/runs/round2-N{1..6}-<ts>/`
- KB 增长：N1 成功补丁归档（新 fingerprint）
- 新增 1 条 ScriptedAnalyzer 规则 + TDD 单测；新增 N6 verify case `lcview_no_fault_service`
- README 同步检查（改 `engineering/harness/` 或 cases 时）

## 7. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| N6 dd 变砖 | 低 | 高 | 无效 oneshot 不 panic；host+device 双备份；四阶段防护；SD 物理重刷兜底（已确认）；dd 前用户确认 |
| Opencode LLM 坏补丁 | 中 | 低 | worktree 隔离 + 白名单 + 语法预检 |
| 临时改 analyzer.yaml threshold | 低 | 低 | N5 结束后立即 `git checkout` 还原 |
| 设备 IP 变化（DHCP） | 高 | 低 | 每次 reboot 后串口 helper 重新发现 |

## 8. 成功标准

1. 框架缺陷#1 修复：全量 `pytest engineering/loop` 稳定绿
2. N1/N2 → DONE_SUCCESS；N3 → `duplicate_patch_hash` ESCALATE；N4 → 收敛态 RETRY 直到 DONE_SUCCESS；N5 → human gate approve 闭环；N6 → DD_BOOT 四阶段防护 + 真机 dd/reboot 走通
3. §5.2 dmesg 基线缺陷修复，长运行稳定
4. 新 report 归档，KB 增长，新 Scripted 规则 + TDD 通过
5. 全量回归（pytest + C++ 单元测试）无回归

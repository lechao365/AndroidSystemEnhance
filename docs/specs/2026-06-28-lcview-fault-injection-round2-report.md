# LcView 故障注入 × Loop Engineering v2 全能力验证（第 2 轮）报告

**日期：** 2026-06-28
**Spec：** `docs/specs/2026-06-28-lcview-fault-injection-round2-design.md`
**Plan：** `docs/plans/2026-06-28-lcview-fault-injection-round2.md`
**状态：** N1–N5 完成；N6（DD_BOOT_REBOOT）降 dry-run（发现 decider `.rc` mode 判定缺陷 §4.5）

---

## 1. 执行概述

本轮在**全新、独立、不复用第 1 轮 F1–F5 注入点**的前提下，按**风险递增顺序**（用户指令：先低后高，遇到问题自主修复，仅高风险/无权限时介入）端到端验证 LE v2 全能力，并修复过程中暴露的框架缺陷。真机：RPi5 `192.168.1.22:5555`（adb 直连）。

## 2. 故障注入验证矩阵

| # | 验证维度 | 注入（不依赖数据流） | 结果 |
|---|---------|---------------------|------|
| **N1** | KB-miss → 新增 ScriptedAnalyzer 规则 | daemon 入口注入特异日志 `parse loop aborted: read-loop fault N1` | ✅ **DONE_SUCCESS**：新规则 `_rule_lcview_parse_loop_break`(conf=0.95) 命中 → PUSH_SINGLE 编译+push+restart → att0 FAIL→att1 PASS |
| **N2** | KB/Scripted-miss → OpencodeAnalyzer LLM | daemon 入口注入 `filewriter degraded`（无 Scripted 规则） | ✅ **DONE_SUCCESS**：`[OpencodeAnalyzer] opencode LLM 生成`(conf=0.8) 真实 spawn opencode 子会话生成删除补丁 → 收敛 |
| **N3** | 三层 analyzer 全空 → 退人工 | 特异日志 + OpencodeAnalyzer 不可用 | ✅ **ESCALATE_HUMAN**：`三层 analyzer 均无产出`(conf=0.0) → 退人工终态 |
| **N4** | progress_converging 收敛态 | 双故障递减（Scripted+LLM） | ⚠️ **端到端未稳定触发**：受 stash 模式 compile/deploy 与 logcat verify 时序交互限制（详见 §4.3）；三态已由单元测试完整覆盖 |
| **N5** | human gate（低置信 pending + approve） | `filewriter degraded` + threshold 临时调 0.9 | ✅ **DONE_SUCCESS**（修复 2 个 approve 框架 bug 后）：触发 `LOW_CONFIDENCE` pending → `approve` → 真正 apply+COMPILE+DEPLOY → 收敛 |
| **N6** | DD_BOOT_REBOOT + 四阶段防护 + serial 回滚 | init.rc 注入 `setprop lechao.fault.n6 injected` | 🔧 **降为 dry-run**：decider 判 `.rc→dd_boot_reboot` 已验证；真机两次 dd **未执行**——发现 decider 对 `.rc` 的 mode 判定与实际安装分区不符（见 §4.5），按用户决策降级，零变砖风险 |

## 3. 已验证 LE 能力清单

- **三层 analyzer**：KnowledgeBaseAnalyzer 降级（N1/N3 KB-miss）、ScriptedAnalyzer 规则命中（N1 conf=0.95）、OpencodeAnalyzer 真实 LLM 子会话（N2 conf=0.8）、ChainedAnalyzer 降级链（N3 三层空）
- **状态机节点**：INIT_SESSION / RUN_VERIFY / DECIDE_NEXT / BUILD_ANALYSIS_REQUEST / WAIT_ANALYZER_PATCH / APPLY_PATCH / COMPILE_PATCH / DEPLOY_PATCH / DONE_SUCCESS / ESCALATE_HUMAN / DONE_FAILURE
- **部署模式**：PUSH_SINGLE（N1/N2/N5 真机编译+push+restart 收敛）；DD_BOOT_REBOOT decider 判定已验证（`.rc → dd_boot_reboot`）
- **human gate**：LOW_CONFIDENCE pending + approve 续跑闭环（修复后）
- **checkpoint/resume**：human gate 暂停 checkpoint + approve resume（修复后回到 APPLY_PATCH）

## 4. 本轮修复的框架缺陷

### 4.1 测试污染（全量 pytest flaky）
**根因：** `test_runtime_engine.py::test_rollback_deploy_uses_adb_endpoint` 用裸 `sys.modules["loop_adb"]=...` 注入假模块、`sys.modules["loop_adb.client"].AdbClient=...` 挂属性，均不经 monkeypatch，测试结束不恢复，污染后续 deploy 模块测试。
**现象：** deploy 目录单跑 41 passed，全量跑 7 个 deploy 测试 FAIL。
**修复：** 改用 `monkeypatch.setitem/setattr` 自动恢复。全量 `pytest engineering/loop` 从 508 passed+7 flaky → **稳定 524 passed**。

### 4.2 §5.2 dmesg 基线退化
**根因：** `lcview_kernel_module_loaded`/`ke02`/`ke03`/`ke15` 依赖 `dmesg | grep 'initialized (ring='`；设备长运行后早期 init 日志被环形缓冲滚出（真机实证 `dmesg | grep -c 'initialized (ring=' = 0`），导致 false-positive FAIL，且 `ke02` 是 kernel_driver 大量 case 的 requires 前置，崩塌整条依赖链。
**修复：** 4 个 case 迁移到 sysfs（`/sys/module/lcview`、`/sys/class/lcview/vendor_lechao_lcview`、`/sys/module/lcview/parameters/ring_size_kb`），不依赖环形缓冲。真机 le run 全 PASS。注：lcview 是 built-in 内核模块（`/proc/modules` 无），故不能用 lsmod。

### 4.3 human gate approve 闭环（2 个 bug）
**bug-1（resume 跳过补丁 apply）：** human gate 在 APPLY_PATCH 的 confidence 检查处 `return`（补丁未 apply），checkpoint 的 `next_node` 经 `_LINEAR_NEXT` 取 COMPILE_PATCH；approve→resume 跳到 COMPILE_PATCH，**跳过实际补丁 apply** → 补丁丢失 → DONE_FAILURE。
**bug-2（approve 缺 endpoint）：** `approve` 子命令 argparse 未定义 `--adb-endpoint`，`_handle_approve→_handle_resume` 访问 `args.adb_endpoint` → `AttributeError` → RUNTIME_FATAL → DONE_FAILURE。
**修复：**
- `_compute_next_node`：human gate 暂停（APPLY_PATCH+LOW_CONFIDENCE）时 `next_node=APPLY_PATCH`（resume 回到 apply 重执行）
- `RuntimeState` 新增 `human_gate_approved` 字段；`_handle_approve` 设 session 标记；`_handle_resume` 传入 engine state；APPLY_PATCH 检查该标记跳过 gate 真正 apply（一次性消费）
- `approve` 子命令补 `--adb-endpoint`；`_handle_resume` 防御性 `getattr`
- 新增 2 个 TDD 测试。真机重验 N5 approve → patch APPLIED+COMPILED+DEPLOYED → DONE_SUCCESS。

### 4.4 新增 ScriptedAnalyzer 规则（能力扩展）
- `_rule_lcview_parse_loop_break`（N1，+4 TDD）
- `_rule_lcview_rc_fault_prop`（N6 .rc 故障，+3 TDD）

### 4.5 decider 对 .rc 的 mode 判定与实际分区不符（N6 发现，待修复）
**现象：** `daemon/lechao_lcview.rc` 经 Android.bp `init_rc: ["lechao_lcview.rc"]` 装到 **`/system/etc/init/lechao_lcview.rc`（system 分区）**（设备实测确认）。但 `loop_deploy.decider` 对含 `lechao_lcview` 且后缀 `.rc` 的文件判 `dd_boot_reboot`（对应 `mk_rpi5_full_image.sh -mode 2` = 仅内核+bootimage）。
**问题：** mode2 不含 system 分区改动 → dd boot 不会让 `.rc` 故障生效，N6 真机闭环无法成立。
**正确语义：** `.rc`（system 分区）应走 `systemimage`（mode4）重新打包 system + 刷 system；或 decider 区分 `.rc` 落点（system/vendor）选对应 image mode。
**本轮处理：** 按用户决策 N6 降 dry-run，该缺陷作为高价值发现记录，不在本轮修复（涉及 decider 逻辑 + 可能的 image mode 扩展）。**建议：** decider 增加 `.rc → init_rc 目标分区` 推断（查 Android.bp `init_rc`/`soc_specific`/`system_ext_specific`），映射到正确 image mode；或 DD_BOOT_REBOOT 内部按文件落点选 mode2/3/4。

## 5. LE 框架验证暴露的真实设备/模块问题（非框架缺陷）

> 这些是 LE 框架"如实验证"的价值体现——框架正确检测到问题、未假通过；问题本身属 lcview 模块/设备/验证设计，按 spec §2.1 非目标不在本轮修复范围，记录待后续。

### 5.1 HAL 开机时序致 AIDL 未注册
设备开机后 `lechao_lcview_hal` 进程 running 但**未注册** `vendor.lechao.lcview.ILcView/default`（`service list`/`lshal` 无）；daemon `bind_hal` 重试 1200 次（120s）后 `return 1` 退出 → `init.svc.lechao_lcview=stopped`（这是 baseline "daemon stopped" 的真因，被 common suite 的容错断言掩盖）。**缓解：** `setprop ctl.restart lechao_lcview_hal` 后 HAL 正常注册。**建议：** 排查 HAL 启动时序 / 注册重试。

### 5.2 lcview kernel 数据流不通
USB 触发（`dd` 读挂载 USB 存储）后 daemon **无 batch**、HAL ring `usage=0B/262144B`、jsonl 不生成、invalid_records.log 为空。即 kernel 模块未产生 lcview 事件（dd 读普通 USB 存储不触发其监听的特定 USB 传输事件）。**影响：** end_to_end suite 的 jsonl 数据链路 case baseline 即 FAIL，N2/N3/N4 原设计（依赖数据流）改用不依赖数据流的启动日志故障。**建议：** 明确 lcview 事件触发条件（特定 USB 设备/操作）。

### 5.3 verify 基于 logcat 缓冲/一次性启动日志脆弱
verify case `grep logcat` 检测启动日志故障，但 deploy 流程末尾 `logcat -c` 清缓冲、daemon 启动序列只执行一次不重打 → 修复部署后 verify 看不到故障日志（假 PASS 风险）。**缓解：** 本轮将捕获 case 改为主动 `logcat -c; setprop ctl.restart; sleep; grep` 重现故障。**建议：** verify 优先用持续可观测状态（sysfs/property/service state）而非一次性日志。

### 5.4 N4 收敛态端到端限制
基于"启动一次性日志 + deploy logcat -c"的故障，attempt0 部分修复后 attempt1 未修故障难稳定重现，叠加 stash 模式 compile/deploy 与 working tree 一致性的时序交互，`progress_converging` 收敛态 2→1→0 未能端到端稳定触发。三态由单元测试 `test_progress_converging_grants_retry/escalates_stuck/escalates_increasing` 完整覆盖。

## 6. 测试统计

- **Python 单元测试：** **524 passed**（含本轮新增：2 测试污染修复验证、4 N1 规则、2 human gate approve、3 N6 规则）
- **端到端真机 session：** N1/N2/N5 DONE_SUCCESS、N3 ESCALATE_HUMAN、N4 多次（含根因调查）
- **真机编译：** `m lechao_lcview` 多次增量编译成功（PUSH_SINGLE 链路）
- **decider 验证：** `.rc→dd_boot_reboot`、`.cpp→push_single`

## 7. 结论

1. **LE v2 框架核心闭环在真机上确证可用**：三层 analyzer（KB 降级 / Scripted 规则 / Opencode LLM）+ PUSH_SINGLE 编译部署收敛 + ESCALATE 退人工 + human gate approve 闭环。
2. **过程中修复 4 类框架缺陷**（测试污染、dmesg 基线、approve 闭环 2 bug），全量 pytest 稳定绿，框架可用性提升。
3. **如实暴露 3 类设备/模块/验证问题**（HAL 时序、数据流、verify 脆弱性），体现框架"不假通过"的验证价值。
4. **N6 DD_BOOT_REBOOT** 降 dry-run：decider 判定已验证；真机 dd 未执行，过程中发现 decider 对 `.rc` 的 mode 判定与实际 system 分区不符（§4.5，待后续修复）。

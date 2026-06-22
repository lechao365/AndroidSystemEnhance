# lciod Loop 自动化验证与 AI 闭环设计

> **日期**：2026-06-22
> **状态**：已确认，待实施计划
> **范围**：为 Loop Engineering 增加 lciod feature 验收 case（覆盖 32 个单设备能力点）+ loop_deploy 部署层（git diff 决策 + mmm/push + boot.img dd/reboot）+ loop_controller AI 闭环（主会话内调度 + LlmAnalyzer 抽象接口 + 预设 bug 演练）。目标覆盖三大诉求：1）验证 lciod 基础能力与新增代码能力；2）case 发现问题后自动改 workspace 代码并根据改动范围决策刷机/推包；3）至少完成一次"定位→修改→打包→上板复测"完整闭环。**不含** 双设备故障注入（Zero2W + RP5 的 12 类故障 F1-F12）、LlmAnalyzer API 实现、vendor dd 在线刷机、DD_BOOT_REBOOT 内核 bug 闭环演练。
> **前序**：基于 `loop_core` 执行层（成熟）、`loop_controller` policy/engine/state 骨架（零接线）、`harness/scripts/mk_rpi5_full_image.sh`（mode 0-4 全覆盖）、`patchs/rpi5/` 下的 lciod 内核驱动 + HAL + Daemon 实现、`02-IO增强` 设计文档（45 能力点）。

---

## 1. 背景与目标

### 1.1 lciod 待验证规模

lciod 是一套四层 USB 存储速率监控框架（内核事件捕获 → 字符设备 ABI → Vendor HAL 代理 → System Daemon 暴露），`02-IO增强` 设计文档定义了 45 个能力点：

| 层 | 能力点数 | 单设备可验证 | 依赖故障注入 |
|----|---------|------------|-------------|
| 内核驱动（02.01） | 16 | 16 | 0 |
| HAL（02.02） | 8 | 8 | 0 |
| Daemon（02.03） | 8 | 8 | 0 |
| 故障注入闭环（02.04 + 02.05） | 13 | 0 | 13（F1-F12） |
| **合计** | **45** | **32** | **13** |

本次范围覆盖 **32 个单设备能力点**。13 个故障注入能力点留待后续双设备阶段。

### 1.2 loop 框架现状（三层 maturity 差异极大）

| 层 | 成熟度 | 说明 |
|----|--------|------|
| loop_core（执行） | ★★★★ | YAML case + 6 断言 + 双 transport + EvidenceBundle + 17 测试 |
| deploy（部署） | ☆☆☆☆ | `le deploy` 是占位返回 1，无编译/刷机/push 能力 |
| controller（闭环） | ☆☆☆☆ | policy.py 39 行骨架零调用，无闭环调度 |
| gen-cases | ☆☆☆☆ | 占位 |

**harness 层有完整编译能力**（`mk_rpi5_full_image.sh` 523 行，mode 0-4）但**完全没和 loop 接线**。

### 1.3 三大诉求与设计决策

| # | 诉求 | 决策（已确认） |
|---|-----|--------------|
| 1 | 验证 lciod 基础能力与新增代码能力 | 全量覆盖 32 单设备能力点，5 个 suite |
| 2 | 自动改 workspace + 决策刷机/推包 | AI 自主闭环；git diff 内容判定 DeployMode（+ case tags 兜底） |
| 3 | 至少一次完整闭环 | 埋 3 个预设 bug 演练；PUSH_SINGLE 模式；不演练 DD_BOOT |

**其他关键决策**：
- 自动化程度：完全自动（AI 自主闭环，N≤5 次升级人工）
- 双设备拓扑：先单设备（仅 RP5）
- 首次 fail 处理：埋预设 bug 验证
- AI 分析实现：抽象 LlmAnalyzer 接口 + 主会话默认实现（stub，不调 API）
- 编译集成：复用 `mk_rpi5_full_image.sh`
- Controller 运行边界：主会话内调度（我 = 调度者 + 默认 Analyzer）
- 部署策略：混合（内核 mode2+dd boot；HAL/Daemon mmm+push 单文件）
- .te 改动：降级 FLASH_FULL（P2 不做，要求人工刷机）
- PUSH_SINGLE 编译：mmm 单模块优先

### 1.4 关键工程约束（来自 rpi5 平台评估）

| 约束 | 结论 | 影响 |
|-----|------|------|
| dm-verity | 已禁用（`BOARD_BUILD_DISABLED_VBMETAIMAGE := true`） | adb remount 可写 /system /vendor |
| A/B slot | 不支持 | 无回滚，dd 失败 = 砖机 |
| fastboot | 不支持（RPi5 是 GPU 固件直接加载内核） | 不能用 fastboot flash |
| boot.img 在线 dd | ✅ 已验证可行（文档 00.5 有流程，boot 分区运行时不挂载） | 内核改动可在线部署 |
| vendor.img 在线 dd | ⚠️ 理论可行未验证，高风险 | P2 不做，.te 改动降级 FLASH_FULL |
| `adb root && adb remount` | ✅ userdebug + 禁 verity 可用 | 单 binary push 秒级生效 |
| SELinux | enforcing，root 域可写块设备 | dd 需要 `adb root` |

---

## 2. 总体架构

### 2.1 三层分层（loop 框架既有三层 + 新增接线）

```text
┌─────────────────────────────────────────────────────────────┐
│  P3: controller (主会话内调度，新增)                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ LoopController                                        │  │
│  │   ├─ run_full_cycle()  主循环（N≤5 次升级人工）         │  │
│  │   ├─ LlmAnalyzer (抽象接口 + 主会话默认实现 stub)       │  │
│  │   ├─ PatchApplier (应用到 workspace + 记录 diff)        │  │
│  │   └─ Policy (复用已有 policy.py: decide_termination)    │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↑ evidence                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ P2: loop_deploy (新增模块)                            │  │
│  │   ├─ DeployDecider (git diff 内容 → mode)              │  │
│  │   ├─ Compiler (调用 mk_rpi5_full_image.sh / mmm)       │  │
│  │   └─ Deployer (adb push 单文件 / dd boot.img + reboot)  │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↑ cases                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ P1: cases/features/lciod/*.yaml (新增 5 suite)        │  │
│  │   ├─ common.yaml       (6 前置检查)                    │  │
│  │   ├─ kernel_driver.yaml (16 内核能力点)                 │  │
│  │   ├─ hal.yaml          (8 HAL 能力点)                  │  │
│  │   ├─ daemon.yaml       (8 Daemon 能力点)               │  │
│  │   └─ end_to_end.yaml   (4 单设备场景)                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↑ 执行                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ loop_core (既有，复用 + 小扩展)                         │  │
│  │   assertion_engine (扩展 +json_field/range/contains_any) │
│  │   executor / collector / transport (adb)              │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
                    ┌─────────────────┐
                    │ RP5 设备 (adb)   │
                    └─────────────────┘
```

### 2.2 闭环数据流（P3 完成后）

```text
1. 用户: "验证 lciod"
2. LoopController.run_full_cycle():
   ├─ phase=verify:   le run --suite features.lciod.* → evidence_bundle.json
   ├─ if all PASS:    结束，输出报告
   ├─ phase=analyze:  LlmAnalyzer.analyze(evidence) → patch_suggestion
   ├─ phase=apply:    PatchApplier.apply(patch_suggestion) → 改 workspace + git diff
   ├─ phase=decide:   DeployDecider.decide(git_diff) → {push_single|dd_boot_reboot|flash_full}
   ├─ phase=deploy:   根据 mode 调 deploy
   │   ├─ push_single: mmm 单模块 → adb remount → adb push <binary> → setprop ctl.restart
   │   └─ dd_boot_reboot: mk_rpi5_full_image.sh -mode 2 → adb push boot.img → dd → reboot → 等待 boot_completed
   ├─ phase=re-verify: le run 失败的 case 子集
   └─ if still FAIL and attempts < 5: 回到 phase=analyze
      if attempts >= 5: 升级人工，输出诊断报告
```

### 2.3 三阶段交付里程碑

| 里程碑 | 验收方式 | 可独立使用 |
|-------|---------|----------|
| **P1 完成** | `le run --suite features.lciod.*` 跑 32 能力点全 PASS（在已部署 lciod 的设备上） | ✅ |
| **P2 完成** | `le deploy --diff-rev HEAD~1` 能根据 diff 自动选择 push/dd 并部署成功 | ✅ |
| **P3 完成** | 埋预设 bug → `le control` 走通完整闭环，至少 1 次"定位→修→部署→复测"PASS | ✅ |

### 2.4 设计原则

1. **不污染 loop_core**：P1 只加 case YAML + 断言扩展（必要的小改动），P2/P3 加新模块，loop_core 现有代码零侵入
2. **复用既有契约**：`policy.py` 已有的 `decide_termination` 直接接线，不重写
3. **evidence 是单一契约**：cases → evidence → analyzer → patch → deploy → re-verify，全部围绕 evidence_bundle.json 流转
4. **遵循源码改动纪律**：所有改动从 `~/workspace/` 开始，patchs/ 单向归档（按 AGENTS.md source-code-modify 规则）
5. **遵循路径管理**：所有新脚本走 `harness-paths.conf`，不硬编码（按 PATH-001）
6. **遵循脚本维测**：`engineering/` 下 bash 脚本接入 observability（按 script-observability 规则）

---

## 3. P1: lciod 验证 cases

### 3.1 Suite 划分（5 个文件，对齐 lcview 组织模式）

```text
engineering/loop/cases/features/lciod/
├── common.yaml          # 前置检查（adb/boot/服务注册/设备节点/工具就绪）
├── kernel_driver.yaml   # 内核驱动 16 能力点（22 case 含参数化）
├── hal.yaml             # HAL 8 能力点（10 case）
├── daemon.yaml          # Daemon 8 能力点（10 case）
└── end_to_end.yaml      # 单设备端到端场景（4 case）
```

### 3.2 断言引擎扩展（必要的小改动）

在 `assertion_engine.py` + `case_loader.py` 增加 3 种断言（lciod 专属需求）：

| 新断言类型 | 用途 | 示例 |
|----------|------|------|
| `json_field` | 解析 JSON 输出，校验指定字段值 | `{type: json_field, path: "stats.stall_count", op: ge, value: 1}` |
| `exit_code_equals` | 校验退出码等于指定值（fault-verify 用 0/5 区分） | `{type: exit_code_equals, value: 0}` |
| `contains_any` | 输出包含列表中任一项（枚举类校验） | `{type: contains_any, values: [running, stopped]}` |

**为何只加这 3 种**：
- `json_field`：fault-verify `--json` 输出 stats，需校验 `stall_count/error_count/corrupt_count` 等数值，现有 regex 脆弱易错
- `exit_code_equals`：fault-verify 的退出码语义化（0=PASS/5=CHECK_FAIL），现有 `exit_code_zero` 不够
- `contains_any`：枚举型状态校验（如 HAL/Daemon 状态可能是 running/stopped/restarting），现有 regex 过度

**支持的 op**（json_field）：`eq` / `ne` / `gt` / `ge` / `lt` / `le` / `exists` / `not_exists`

### 3.3 32 能力点 → case 完整映射表

#### 3.3.1 common.yaml（6 个 case，前提条件）

| case id | 能力点 | 命令 | 断言 |
|---------|-------|------|------|
| `adb_shell_reachable` | adb 连通性 | `echo lciod_adb_ok` | contains |
| `boot_completed` | 系统启动完成 | `getprop sys.boot_completed` | contains "1" |
| `fault_verify_present` | [code] usb-verify 二进制就位 | `which fault-verify \|\| find /vendor /system -name fault-verify` | not_contains "empty" |
| `lciod_hal_service_registered` | [spec 17] HAL 服务注册 | `service list \| grep vendor.lechao.lciod.IIoHal` | contains "IIoHal" |
| `lciod_daemon_service_registered` | [spec 25] Daemon 服务注册 | `service list \| grep system.lechao.lciod.IIoService` | contains "IIoService" |
| `lciod_device_node_present` | [spec 2,15] 设备节点存在（动态，需先插 U 盘） | `ls -l /dev/vendor_lechao_usbd* 2>/dev/null \|\| echo NO_NODE` | not_contains "NO_NODE" |

**final_collectors**：`lciod_hal_logcat` / `lciod_daemon_logcat` / `lciod_kmsg` / `lciod_runtime_context`

#### 3.3.2 kernel_driver.yaml（16 能力点，22 case）

| case id | 能力点# | 验证内容 | 命令要点 | 断言 |
|---------|--------|---------|---------|------|
| `kernel_module_loaded` | 1 | lciod_usbd 模块加载 | `lsmod \| grep lciod_usbd` | contains |
| `notifier_injection_no_crash` | 1 | 注入零侵入，核心不崩 | `dmesg \| grep -c "usb-storage.*panic\|oops"` | equals "0" |
| `cdev_region_registered` | 2 | 字符设备区域注册 | `ls /sys/class/... \| grep vendor_lechao_usbd` | regex |
| `device_node_0666` | 2,15 | 节点权限 0666 | `ls -l /dev/vendor_lechao_usbd0 \| awk '{print $1}'` | contains "rw-rw-rw-" |
| `device_node_selinux_label` | 15 | SELinux 标签 | `ls -Z /dev/vendor_lechao_usbd0` | contains "lechao_lciod_hal_device" |
| `ida_minor_allocation` | 2 | IDA 次设备号递增 | 插 2 个 U 盘后 `ls /dev/vendor_lechao_usbd*` | regex 匹配 N 和 N+1 |
| `hotplug_probe_on_insert` | 3 | 插入时 PROBE | `dmesg \| grep "registered device vendor_lechao_usbd"` | contains |
| `hotplug_disconnect_on_remove` | 3 | 拔出时 DISCONNECT | 拔 U 盘后 `dmesg \| grep "removed device"` | contains |
| `stats_read_bytes_nonzero` | 4 | read_bytes 累计 | `fault-verify stats get --minor 0 --json` | json_field path=stats.read_bytes op=gt value=0 |
| `stats_write_bytes_nonzero` | 4 | write_bytes 累计 | dd 写入后 stats get | json_field |
| `stats_read_cmds_nonzero` | 4 | read_cmds 累计 | 同上 | json_field |
| `current_rate_calculated` | 5 | 瞬时速率计算 | stats get --json | json_field path=stats.current_rate op=gt value=0 |
| `peak_rate_monotonic` | 5 | peak_rate 单调递增 | 两次 stats get 对比 | json_field（需 host 侧脚本辅助） |
| `degrade_detection` | 6 | degrade 自动检测（单设备模拟慢读） | `dd bs=1 count=1 iflag=fullblock` 慢读后 stats get | json_field path=stats.degrade_count op=ge value=0 |
| `event_buffer_overflow_handled` | 7 | 环形缓冲区溢出处理 | 高频读后 stats get，查 event_drop_count | json_field path=stats.event_drop_count op=ge value=0 |
| `blocking_read_returns_data` | 8 | 阻塞 read 返回数据 | `fault-verify event read --minor 0 --timeout 3000` | exit_code_equals 0 |
| `poll_multiplex_supported` | 8 | poll 多路复用 | `fault-verify event read`（内部已用 poll） | exit_code_equals 0 |
| `graceful_shutdown_on_disconnect` | 9 | 断开返回 EOF | 拔 U 盘后 event read | contains "shutdown\|EOF" |
| `kref_lifecycle_no_leak` | 10 | kref 无泄漏 | 多次 open/close 后 `ls /proc/*/fd \| grep vendor_lechao \| wc -l` | equals "0" |
| `ioctl_get_stats` | 12 | IOC_GET_STATS | `fault-verify stats get --minor 0` | exit_code_equals 0 |
| `ioctl_reset_state` | 12,13 | IOC_RESET_STATE + 保留 probe/disconnect_count | reset 前后对比 probe_count | json_field（host 脚本） |
| `ioctl_get_config` | 12 | IOC_GET_CONFIG | `fault-verify config get --minor 0` | exit_code_equals 0 |
| `ioctl_set_config` | 12 | IOC_SET_CONFIG | `fault-verify config set --minor 0 --enabled 1` 后 get 校验 | exit_code_equals 0 |
| `lcview_events_emitted` | 14 | LcView 9 事件打点 | 查 `/data/vendor/lechao_lcview/logs/*.jsonl` 含 USB_* 事件 | regex |
| `dmesg_registered_log` | 16 | dmesg 启动日志 | `dmesg \| grep "registered device vendor_lechao_usbd.*VID.*PID"` | regex |

**异常事件推送**（6 类，能力点 11）由于依赖故障注入，在单设备阶段标记为 `severity: warn` + `tags: [requires_injection]`，仅做能力存在性探测（`fault-verify event read` 能读到事件类型字段即可），不在 P1 强求具体故障触发。

#### 3.3.3 hal.yaml（8 能力点，10 case）

| case id | 能力点# | 验证内容 | 断言 |
|---------|--------|---------|------|
| `hal_process_running` | 17 | HAL 进程存活 | contains "running" |
| `hal_vintf_manifest` | 18 | VINTF manifest 存在 | contains |
| `hal_list_devices` | 19 | listDevices 返回设备列表 | exit_code_equals 0 |
| `hal_get_stats` | 20,22 | getStats 字段映射正确 | json_field |
| `hal_reset_state` | 20 | resetState 成功 | exit_code_equals 0 |
| `hal_get_config` | 20 | getConfig 成功 | exit_code_equals 0 |
| `hal_set_config` | 20 | setConfig 成功 + 回读校验 | json_field |
| `hal_read_event` | 20,21 | readEvent 排空策略 | exit_code_equals 0 |
| `hal_persistent_fd` | 20 | 持久 fd（连续 readEvent 不重新 open） | exit_code_equals 0（连续 2 次 read） |
| `hal_single_binder_thread` | 23 | 单 Binder 线程 | `service check` 不阻塞并发调用 |

#### 3.3.4 daemon.yaml（8 能力点，10 case）

| case id | 能力点# | 验证内容 | 断言 |
|---------|--------|---------|------|
| `daemon_process_running` | 25 | daemon 进程存活 | contains "running" |
| `daemon_boot_completed_trigger` | 26 | boot_completed 后启动 | getprop init.svc + requires boot_completed |
| `daemon_field_projection` | 27 | 字段投影（system IoStats 无 currentRate/enabled/flags） | json_field 检查字段不存在 |
| `daemon_get_average_rate` | 28 | getAverageRate 派生计算 | json_field path=average_rate op=gt value=0 |
| `daemon_hal_lazy_connect` | 29 | HAL 延迟连接 | `fault-verify` 经 daemon 路径调用成功 |
| `daemon_death_reconnect` | 30 | 死亡重连 | kill HAL 后 daemon 自动重连（host 脚本触发） |
| `daemon_monitor_thread_stats` | 31 | 监控线程 10s 周期统计输出 | `logcat -s lechao_lciod:V \| grep "monitor\|stats"` |
| `daemon_multi_device_iterate` | 32 | 多设备遍历 | 多 U 盘场景下 stats get 各 minor 都成功 |

#### 3.3.5 end_to_end.yaml（单设备可做部分，4 case）

| case id | 场景 | 验证内容 |
|---------|------|---------|
| `e2e_stats_reset_and_check` | stats reset → dd → check stats --read-ge 1 | exit_code_equals 0 |
| `e2e_config_toggle` | config set enabled=0 → dd → stats 无变化 → config set enabled=1 → dd → stats 增长 | json_field 两次对比 |
| `e2e_event_flow` | stats reset → dd → event read → 校验事件类型是 TRANSPORT_START/END | json_field path=event.type |
| `e2e_daemon_proxy_path` | 通过 daemon IIoService 路径调用 getStats → 与 HAL 直连结果一致 | json_field 两次对比 |

**故障注入 5 场景**（STALL/Timeout/CSW Corrupt/Hotplug/Degrade）：单设备阶段不做，标记为 `requires_injection`，留待后续阶段。

### 3.4 Collector 设计（lciod 专属）

```yaml
collectors:
  lciod_hal_logcat:
    mode: adb_logcat
    filters: ["lechao_lciod_hal"]
    hints: "HAL 层日志，检查 ioctl/connect/readEvent 错误"
  lciod_daemon_logcat:
    mode: adb_logcat
    filters: ["lechao_lciod"]
    hints: "Daemon 层日志，检查 monitor thread / HAL client 重连"
  lciod_kmsg:
    commands: ["dmesg | grep -i 'vendor_lechao_usbd\\|lciod'"]
    hints: "内核侧 lciod 驱动日志"
  lciod_fault_verify_json:
    commands: ["fault-verify stats get --minor 0 --json || true"]
    hints: "fault-verify 完整 stats JSON 快照"
  lciod_device_state:
    commands:
      - "ls -l /dev/vendor_lechao_usbd*"
      - "ls -Z /dev/vendor_lechao_usbd*"
      - "service list | grep lechao"
    hints: "设备节点 + 服务注册快照"
```

### 3.5 USB 设备依赖处理

部分 case 需要真实 U 盘插入（触发 PROBE、生成 stats、读 event）。设计：
- **前置 case** `usb_storage_inserted`：检测 `mount | grep media_rw` 是否有 USB 存储挂载。若失败给出 hints"请插入 USB 存储设备"
- 所有依赖真实 USB 的 case `requires: [usb_storage_inserted]`
- **触发 IO 的方式**：`dd if=/mnt/media_rw/<usb>/testfile of=/dev/null bs=1M`（与 lcview e2e 一致）
- 需要在设备上预置测试文件（后续 case 中 `dd if=/dev/zero of=$USB/test_100m bs=1M count=100` 预置）

### 3.6 P1 验收标准

1. `le run --suite features.lciod.common` → 6 case 全 PASS（在已部署 lciod + 插入 U 盘的设备上）
2. `le run --suite features.lciod.kernel_driver` → 22 case 全 PASS
3. `le run --suite features.lciod.hal` → 10 case 全 PASS
4. `le run --suite features.lciod.daemon` → 10 case 全 PASS
5. `le run --suite features.lciod.end_to_end` → 4 case 全 PASS
6. EvidenceBundle 完整输出（含 fault-verify JSON、logcat、kmsg）
7. 新增 3 种断言类型有对应单测（assertion_engine 的既有测试模式）

### 3.7 P1 文件变更清单

| 文件 | 操作 | 说明 |
|-----|------|------|
| `engineering/loop/cases/features/lciod/common.yaml` | 新增 | 6 case + 5 collector |
| `engineering/loop/cases/features/lciod/kernel_driver.yaml` | 新增 | 22 case |
| `engineering/loop/cases/features/lciod/hal.yaml` | 新增 | 10 case |
| `engineering/loop/cases/features/lciod/daemon.yaml` | 新增 | 10 case |
| `engineering/loop/cases/features/lciod/end_to_end.yaml` | 新增 | 4 case |
| `engineering/loop/core/python/loop_core/assertion_engine.py` | 修改 | +3 断言类型（json_field / exit_code_equals / contains_any） |
| `engineering/loop/core/python/loop_core/case_loader.py` | 修改 | `_VALID_ASSERT_TYPES` 增加 3 类 + 断言形状校验 |
| `engineering/loop/core/python/loop_core/tests/test_assertion_engine.py` | 修改 | 新断言的单测 |
| `engineering/loop/templates/case-template.md` | 修改 | 断言矩阵补 3 类 |
| `engineering/loop/README.md` | 修改 | lciod suite 索引 |

**注**：所有改动在 `engineering/loop/` 下，不碰 `~/workspace/` 源码（P1 不涉及 lciod 本身改动）。

---

## 4. P2: loop_deploy 部署层

### 4.1 模块边界与依赖

```text
engineering/loop/deploy/                         # 新增目录
├── python/loop_deploy/
│   ├── __init__.py
│   ├── cli.py                                   # deploy 子命令逻辑（被 loop_core.cli 调用）
│   ├── decider.py                               # DeployDecider: git diff → DeployPlan
│   ├── compiler.py                              # Compiler: 调 mk_rpi5_full_image.sh / mmm
│   ├── deployer.py                              # Deployer: push_single / dd_boot_reboot
│   ├── adb_ops.py                               # adb 操作封装（push/remount/restart_service/wait_boot）
│   └── models.py                                # DeployPlan / DeployResult / DeployMode
└── tests/
    ├── test_decider.py
    ├── test_deployer.py
    └── test_models.py
```

**依赖关系**（无环）：

```text
loop_deploy.cli
  ├─→ loop_deploy.decider   (git diff → DeployPlan)
  ├─→ loop_deploy.compiler   (DeployPlan → build artifacts)
  ├─→ loop_deploy.deployer   (DeployPlan + artifacts → device)
  │     └─→ loop_deploy.adb_ops  (复用 + 扩展 loop_adb.client)
  └─→ loop_core.host_exec    (复用，编译命令执行)
```

### 4.2 DeployDecider：git diff → DeployMode 决策

**输入**：`git diff` 的变更文件清单（从 workspace 的 git 工作区获取）
**输出**：`DeployPlan` dataclass

```python
@dataclass
class DeployPlan:
    mode: DeployMode                # SKIP / PUSH_SINGLE / DD_BOOT_REBOOT / FLASH_FULL
    changed_files: list[str]        # 变更文件相对路径
    reason: str                     # 判定理由（可解释性）
    build_targets: list[str]        # 编译目标（mmm 模块 或 mk_rpi5 mode）
    deploy_targets: list[DeployTarget]  # 部署目标（push 路径/dd 设备）
    requires_reboot: bool           # 是否需要重启
    estimated_seconds: int          # 预估耗时（决策日志用）

class DeployMode(StrEnum):
    SKIP = "skip"                   # 无改动或纯文档
    PUSH_SINGLE = "push_single"     # 单 binary push（HAL/Daemon .cpp）
    DD_BOOT_REBOOT = "dd_boot"      # boot.img dd + reboot（内核/init.rc）
    FLASH_FULL = "flash_full"       # 全量刷机（多分区同时改动 / .te 改动，P2 不实现，仅占位）
```

### 4.3 DeployDecider 决策规则（按优先级从高到低）

| 优先级 | diff 命中的文件模式 | DeployMode | build_target | deploy_target | reboot |
|-------|-------------------|-----------|--------------|--------------|--------|
| 1 | `kernel/**/lciod_usbd*.c\|h` | DD_BOOT_REBOOT | `mk_rpi5_full_image.sh -mode 2` | `dd boot.img → mmcblk0p1` | ✅ |
| 2 | `kernel/**/defconfig*` | DD_BOOT_REBOOT | mode 2 | dd boot.img | ✅ |
| 3 | `kernel/**/usb/storage/*.diff` | DD_BOOT_REBOOT | mode 2 | dd boot.img | ✅ |
| 4 | `**/sepolicy/*.te` | FLASH_FULL | （P2 不实现） | 要求人工刷机 | ✅ |
| 5 | `**/lechao_lciod*.rc` | DD_BOOT_REBOOT | mode 2 | dd boot.img | ✅ |
| 6 | `**/lechao_lciod*/**/*.cpp` | PUSH_SINGLE | `mmm vendor/lechao/services/lechao_lciod` | push binary → vendor/bin | ❌（restart service） |
| 7 | `**/lechao_lciod*/**/Android.bp` | PUSH_SINGLE | mmm 同上 | push binary | ❌ |
| 8 | `others/usb-verify/**` | PUSH_SINGLE | 独立 make | push fault-verify | ❌ |
| 9 | 纯 `.md`/`.yaml`/docs | SKIP | 无 | 无 | ❌ |
| 10 | 多分区同时命中 | FLASH_FULL | mode 1 | （P2 占位，要求人工） | ✅ |

**.te 文件降级策略**：rpi5 的 sepolicy 编入 vendor.img，运行时无法热替换（SELinux 策略在编译时生成二进制）。.te 改动**必须 mode 3 编译 vendor.img + dd vendor**。由于 vendor.img dd 未验证（高风险），**P2 阶段降级为 FLASH_FULL（要求人工刷机）**，P3 验证 vendor dd 可行性后升级。

### 4.4 PUSH_SINGLE 流程（mmm + push + restart）

```python
def deploy_push_single(self, plan: DeployPlan, artifacts: list[str]):
    target = plan.deploy_targets[0]
    # 1. adb root + adb remount（userdebug + 已禁 verity，可写 /vendor /system）
    self.adb.root(); self.adb.remount()
    # 2. adb push <local_binary> <remote_path>
    self.adb.push(artifacts[0], target.remote_path)
    # 3. setprop ctl.restart <service>（秒级生效，不 reboot）
    self.adb.shell(f"setprop ctl.restart {target.service_name}")
    # 4. 等待服务恢复（轮询 init.svc.<name> == running，超时 15s）
    self._wait_service_running(target.service_name, timeout=15)
    return DeployResult(success=True, mode=PUSH_SINGLE, duration_sec=...)
```

**预期耗时**：< 5 分钟（mmm 编译 1-3min + push/restart < 30s）

### 4.5 DD_BOOT_REBOOT 流程（mode2 + dd + reboot）

```python
def deploy_dd_boot(self, plan: DeployPlan, artifacts: list[str]):
    boot_img = artifacts[0]
    # 1. 校验 boot.img 存在且非空
    # 2. adb root
    # 3. adb push boot.img → /data/local/tmp/boot.img（userdata rw 可写）
    self.adb.push(boot_img, "/data/local/tmp/boot.img")
    # 4. sha256 校验（push 后 vs host 侧）
    host_sha = sha256(boot_img); remote_sha = adb.shell("sha256sum /data/local/tmp/boot.img")
    if host_sha != remote_sha: raise DeployError("sha mismatch")
    # 5. dd if=/data/local/tmp/boot.img of=/dev/block/mmcblk0p1 bs=4M（root shell）
    self.adb.shell("dd if=/data/local/tmp/boot.img of=/dev/block/mmcblk0p1 bs=4M", as_root=True)
    # 6. sync
    self.adb.shell("sync", as_root=True)
    # 7. 清理临时文件
    self.adb.shell("rm /data/local/tmp/boot.img", as_root=True)
    # 8. adb reboot
    self.adb.reboot()
    # 9. 等待 boot_completed（轮询 sys.boot_completed=1，超时 120s）
    self._wait_boot_completed(timeout=120)
    # 10. 重新 adb connect（网络 adb 重启后会断开）
    self.adb.connect()
    return DeployResult(success=True, mode=DD_BOOT_REBOOT, requires_reboot=True)
```

**预期耗时**：< 30 分钟（mode 2 编译 10-25min + push/dd/reboot < 5min）

### 4.6 AdbClient 扩展（+push +remount）

AdbClient 当前有 shell/root/pull/reboot/logcat，**缺 push 和 remount**。P2 在 `loop_adb/client.py` 增加：

```python
def push(self, local_path: str, remote_path: str, timeout_sec: float) -> AdbCommandResult:
    """adb -s <serial> push <local> <remote>。"""
    return self._runner(["adb", "-s", self.device_serial, "push", local_path, remote_path], timeout_sec)

def remount(self, timeout_sec: float) -> AdbCommandResult:
    """adb -s <serial> remount。"""
    return self._runner(["adb", "-s", self.device_serial, "remount"], timeout_sec)
```

这两个方法是 AdbClient 的自然扩展，不破坏现有接口，有对应单测。

### 4.7 CLI 接入：`le deploy`

`loop_core/cli.py` 的 deploy 占位替换为：

```bash
# 查看决策（dry-run，不执行）
le deploy --decide --diff-rev HEAD~1
# 输出: mode=PUSH_SINGLE, changed=[hal/device_io.cpp], target=vendor/bin/hw/lechao_lciod_hal

# 执行部署（完整流程）
le deploy --diff-rev HEAD~1 --adb-endpoint 192.168.1.55:5555

# 仅部署不编译（artifacts 已就绪）
le deploy --mode push_single --artifact /path/to/binary --remote /vendor/bin/hw/lechao_lciod_hal --service lechao_lciod_hal
```

**参数**：
- `--diff-rev <rev>`：git diff 的基准（默认 HEAD~1，即最近一次 commit 的改动）
- `--decide`：仅输出决策不执行（dry-run）
- `--mode <mode>`：跳过决策器，强制指定模式
- `--artifact/--remote/--service`：mode=push_single 时的手动参数
- `--adb-endpoint/--adb-serial`：复用 run 的参数

### 4.8 安全护栏

1. **砖机保护**：DD_BOOT_REBOOT 前必须 sha256 校验 + dd 后 sync + 保留 rollback boot.img
2. **误操作防护**：FLASH_FULL 模式 P2 不实现，命中则报错"需要人工刷机"（返回 DEPLOY_FATAL）
3. **workspace 干净度检查**：deploy 前检查 `git status`，有未提交改动时警告（不阻断，因为 controller apply patch 后 workspace 就是脏的）
4. **编译失败保护**：compiler 失败立即中止，不进入 deploy 阶段
5. **部署失败回滚**：PUSH_SINGLE 失败无副作用（原 binary 还在）；DD_BOOT_REBOOT 失败在 push/dd 阶段可安全重试，reboot 后失败则升级人工

### 4.9 P2 验收标准

1. `le deploy --decide --diff-rev HEAD~1` 能正确识别 4 类改动（kernel/.cpp/.te/docs）并输出正确 DeployMode
2. HAL .cpp 改动场景：`le deploy --diff-rev HEAD~1` 完成 mmm 编译 + push + restart，设备上 `getprop init.svc.lechao_lciod_hal` 恢复 running，**总耗时 < 5 分钟**
3. kernel .c 改动场景：`le deploy --diff-rev HEAD~1` 完成 mode 2 编译 + push boot.img + dd + reboot，重启后设备正常启动，**总耗时 < 30 分钟**
4. AdbClient.push / remount 有单测覆盖
5. DeployDecider 的 10 条规则有单测覆盖（每条规则一个 test case）
6. sha256 校验失败时 deploy 中止并报错

### 4.10 P2 文件变更清单

| 文件 | 操作 | 说明 |
|-----|------|------|
| `engineering/loop/deploy/python/loop_deploy/__init__.py` | 新增 | 包入口 |
| `engineering/loop/deploy/python/loop_deploy/models.py` | 新增 | DeployPlan/DeployMode/DeployResult |
| `engineering/loop/deploy/python/loop_deploy/decider.py` | 新增 | git diff → DeployPlan |
| `engineering/loop/deploy/python/loop_deploy/compiler.py` | 新增 | 调 mk_rpi5 / mmm |
| `engineering/loop/deploy/python/loop_deploy/deployer.py` | 新增 | push_single / dd_boot 两种部署流程 |
| `engineering/loop/deploy/python/loop_deploy/adb_ops.py` | 新增 | wait_service / wait_boot_completed 辅助 |
| `engineering/loop/deploy/python/loop_deploy/cli.py` | 新增 | deploy 子命令参数解析 + 编排 |
| `engineering/loop/deploy/python/tests/test_decider.py` | 新增 | 10 条决策规则单测 |
| `engineering/loop/deploy/python/tests/test_deployer.py` | 新增 | push/dd 流程单测（mock adb） |
| `engineering/loop/connection/providers/adb/python/loop_adb/client.py` | **修改** | +push +remount 方法 |
| `engineering/loop/connection/providers/adb/python/tests/test_client.py` | **修改** | push/remount 单测 |
| `engineering/loop/core/python/loop_core/cli.py` | **修改** | deploy 占位 → 接入 loop_deploy.cli |
| `engineering/harness/config/harness-paths.conf` | **修改** | +LOOP_DEPLOY_DIR（deploy 包根） |
| `engineering/loop/README.md` | **修改** | deploy 层索引 |
| `engineering/loop/deploy/README.md` | 新增 | deploy 层文档 |

**注**：所有改动在 `engineering/loop/` 和 `engineering/harness/config/` 下，不碰 `~/workspace/` 源码。

---

## 5. P3: loop_controller AI 闭环

### 5.1 核心设计决策：主会话内调度的协议

基于"主会话内调度 + 抽象接口 + 默认主会话 stub 实现"，闭环采用**分阶段 CLI + 文件交接协议**：

```text
┌──────────────────────────────────────────────────────────────┐
│  主会话（GLM-5.2）= 调度者 + 默认 LlmAnalyzer stub             │
│                                                               │
│  bash ── le control init ──────────────────► session.json     │
│  bash ── le control run-verify ───────────► evidence_bundle   │
│  Read ── analysis_request.json ◄────────── (controller 生成)   │
│  Read ── evidence_bundle.json ◄────────── (失败 case 详情)     │
│  【我分析失败原因，用 Edit 改 workspace 代码】                  │
│  bash ── le deploy --diff-rev HEAD ──────► 编译+部署+重启      │
│  bash ── le control run-verify ──────────► 复测 evidence      │
│  bash ── le control decide ─────────────► RETRY / STOP        │
└──────────────────────────────────────────────────────────────┘
```

**关键点**：主会话（我）在闭环中承担两个角色——**调度者**（按 SOP 调 CLI）和**默认 Analyzer**（读 evidence、分析、Edit 代码）。controller 库提供状态追踪 + 政策判定 + 编排辅助，**不调用任何 LLM API**（零外部依赖）。

### 5.2 controller 库扩展（复用既有 policy/engine/state）

既有 controller 三件套（policy/engine/state）**零改动**，新增 4 个模块：

```text
engineering/loop/controller/python/loop_controller/
├── __init__.py          # 既有，导出扩展
├── policy.py            # 既有，零改动（decide_termination 已完备）
├── engine.py            # 既有，零改动（apply_stage_result）
├── state.py             # 既有，零改动（new_session）
├── cycle_orchestrator.py    # 新增：分阶段编排（run_verify / record_attempt / decide_next）
├── analyzer_protocol.py     # 新增：LlmAnalyzer ABC + AnalysisRequest / PatchSuggestion 模型
├── patch_applier.py         # 新增：结构化补丁应用（FileChange → workspace edit）
└── control_cli.py           # 新增：le control 子命令
```

### 5.3 analyzer_protocol.py（抽象接口 + 数据模型）

```python
class LlmAnalyzer(ABC):
    """LLM 分析器抽象接口。
    
    默认实现是主会话本身（不走代码），此接口供未来接 API/子进程扩展。
    """
    @abstractmethod
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion: ...

@dataclass
class AnalysisRequest:
    """controller → 主会话的分析请求（序列化为 JSON 文件交接）。"""
    session_id: str
    attempt_index: int
    failed_cases: list[dict]          # 失败 case 的 id/reason/command/output_snippet
    evidence_bundle_path: str         # 完整 evidence 路径
    collectors_output: dict           # on_fail collectors 采集的诊断数据
    workspace_diff_so_far: str        # 本 session 已应用的补丁累计 diff
    hints: str                        # controller 给 analyzer 的提示

@dataclass
class PatchSuggestion:
    """主会话 → controller 的补丁建议。"""
    target_files: list[FileChange]
    rationale: str
    confidence: float                 # 0.0-1.0
    deploy_mode_hint: str             # analyzer 建议的部署模式（decider 二次校验）

@dataclass
class FileChange:
    workspace_path: str               # ~/workspace/ 内相对路径
    change_type: Literal["edit", "create", "delete"]
    old_marker: str                   # edit: 要替换的旧代码片段（唯一匹配）
    new_content: str                  # 替换为的新代码
```

**主会话实现 = 不写代码**：主会话（我）直接用 Edit 工具操作 workspace，不走 PatchSuggestion 序列化。`analyzer_protocol.py` 中的 `LlmAnalyzer` ABC 是给"未来接 API"留的扩展点，P3 阶段有一个 `MainSessionAnalyzer` stub，其 `analyze()` 抛出 `"主会话模式下请直接用 Edit 工具操作 workspace"` 提示。

### 5.4 patch_applier.py（补丁应用器）

提供 `apply_file_changes(changes, workspace_root) -> ApplyResult` 函数，**精确字符串匹配替换**（与 Edit 工具语义一致）：
- 校验 `old_marker` 在目标文件唯一匹配
- 替换为 `new_content`
- 返回 `{success, applied_files, git_diff, error}`
- 失败时**不部分应用**（原子性）

**主会话可选择**：用 PatchApplier（结构化）或直接用 Edit 工具（灵活）。P3 演练阶段主会话用 Edit 工具为主，PatchApplier 作为"可复现"的补档路径。

### 5.5 cycle_orchestrator.py（编排辅助）

```python
def run_verify_phase(session, suite, cli_args) -> StageResult:
    """调 le run，解析 evidence_bundle.json，更新 session。"""
    
def build_analysis_request(session) -> AnalysisRequest:
    """从当前 attempt 的失败 case 构造分析请求，写 analysis_request.json。"""

def record_attempt(session, *, verify_result, patch_diff, deploy_result) -> SessionState:
    """记录一次完整 attempt（verify→patch→deploy→re-verify）。"""

def decide_next(session) -> TerminationDecision:
    """复用 policy.decide_termination，判定 RETRY/STOP/ESCALATE。"""
```

### 5.6 `le control` 子命令

```bash
# 初始化 session（创建 artifacts 目录 + session.json）
le control init --target lciod --max-attempts 5
  → 输出 session_id（如 lciod-20260622-001）+ artifacts 路径

# 执行一次验证（调 le run，更新 session）
le control run-verify --session <id> --suite features.lciod.* [--adb-endpoint ...]
  → 输出 PASS/FAIL + 失败 case 数

# 生成分析请求（FAIL 时调用）
le control analyze-request --session <id>
  → 输出 analysis_request.json 路径 + 失败 case 摘要到 stdout

# 部署当前改动（主会话 Edit 后调用）
le control deploy --session <id> [--adb-endpoint ...]
  → 调 le deploy --diff-rev HEAD，编译+部署+可能的 reboot

# 判定下一步
le control decide --session <id>
  → 输出 RETRY / STOP / ESCALATE + 理由

# 查看状态
le control status --session <id>
  → 输出 session.json（attempts 历史、当前状态）
```

### 5.7 预设 bug 方案（验证闭环能力）

**设计原则**：3 个 bug 全部在 `.cpp` 文件（PUSH_SINGLE 可修复，安全快速），覆盖 HAL/Daemon 两层，被 P1 的 json_field/exit_code 断言精准捕获。

#### Bug 1: HAL getStats 字段映射反转（HAL 层）

- **位置**：`~/workspace/.../lechao_lciod/hal/device_io.cpp` 的 `getStats()` 字段映射
- **缺陷**：`read_bytes` 与 `write_bytes` 赋值互换
- **被谁抓到**：`hal.yaml` 的 `hal_get_stats` case（json_field path=stats.read_bytes op=gt value=0，由于只做 dd 读不写，write_bytes 期望小，反转后 read_bytes 读到 write_bytes 的值，校验失败）
- **修复**：交换两个字段赋值，mmm 编译，push lechao_lciod_hal，restart 服务
- **DeployMode**：PUSH_SINGLE
- **定位难度**：低（evidence 中 json_field 的 reason 会直接显示期望值 vs 实际值）

#### Bug 2: Daemon getAverageRate 公式错误（Daemon 层）

- **位置**：`~/workspace/.../lechao_lciod/daemon/service.cpp` 的 `getAverageRate()`
- **缺陷**：公式写成 `(readNs+writeNs)*1e9/(readBytes+writeBytes)`（分子分母颠倒，结果是 ns/byte 而非 byte/ns）
- **被谁抓到**：`daemon.yaml` 的 `daemon_get_average_rate` case（json_field path=average_rate op=gt value=0，颠倒后算出极小值 ≈ 0.0000x，校验失败）
- **修复**：修正公式为 `(readBytes+writeBytes)*1e9/(readNs+writeNs)`
- **DeployMode**：PUSH_SINGLE（system/bin/lechao_lciod）
- **定位难度**：中（需要从 json_field 失败值反推公式错误，evidence 提供实际计算值）

#### Bug 3: HAL readEvent 排空逻辑遗漏（HAL 层 + 时序）

- **位置**：`~/workspace/.../lechao_lciod/hal/device_io.cpp` 的 `readEvent()`
- **缺陷**：poll 后只 read 一次就返回，不循环排空（违反设计 02.02 第 21 点"排空策略"）
- **被谁抓到**：`hal.yaml` 的 `hal_persistent_fd` case（连续 2 次 readEvent，第二次应读到更新的事件，但因不排空，第二次读到旧事件或无事件，json_field 对比失败）
- **修复**：在 read 成功后加 `while (poll(...)==POLLIN) { read(...); }` 排空循环
- **DeployMode**：PUSH_SINGLE
- **定位难度**：高（需要理解"排空策略"设计意图，evidence 中会显示 2 次 read 返回相同事件）

**埋 bug 方式**：用一个 `apply_preset_bugs.sh` 脚本，接受 `--bug 1,2,3` 参数，从 patchs 的"正确代码"反向生成 bug patch 应用到 workspace。脚本记录原始版本便于回滚。

### 5.8 完整闭环演练 SOP（主会话执行）

```text
[预设] apply_preset_bugs.sh --bug 1  # 在 workspace 埋 Bug 1

[步骤 1] le control init --target lciod --max-attempts 5
         → session_id = lciod-20260622-001

[步骤 2] le control run-verify --session <id> --suite features.lciod.hal
         → FAIL（hal_get_stats 失败，read_bytes 校验不通过）

[步骤 3] le control analyze-request --session <id>
         → 输出: analysis_request.json
           {failed_cases: [{id: hal_get_stats, 
                            reason: "json_field stats.read_bytes op=gt value=0 failed, 
                                     actual=0 (expected >0)",
                            command: "fault-verify stats get --minor 0 --json"}]}

[步骤 4] 主会话 Read analysis_request.json + evidence_bundle.json
         主会话分析: "read_bytes=0 但 write_bytes 可能 >0，疑似字段反转"
         主会话 Grep workspace 找 getStats 字段映射
         主会话 Edit device_io.cpp 交换 read_bytes/write_bytes 赋值

[步骤 5] le control deploy --session <id>
         → le deploy --decide: diff 命中 *.cpp → PUSH_SINGLE
         → mmm 编译 → adb push lechao_lciod_hal → restart → 等待 running

[步骤 6] le control run-verify --session <id> --suite features.lciod.hal
         → PASS（hal_get_stats 通过）

[步骤 7] le control decide --session <id>
         → STOP (reason: verification passed)

[步骤 8] 主会话归档: workspace diff → patchs/ 单向归档（按 source-code-modify 规则）
         le control status --session <id> → 输出最终报告
```

### 5.9 P3 验收标准（严格）

1. **3 个预设 bug 各走通一次完整闭环**（定位→改→编译→部署→复测 PASS）
2. **Bug 1 闭环耗时 < 10 分钟**（PUSH_SINGLE 模式，含 mmm 编译）
3. **Bug 3（高难度）闭环内 attempt ≤ 2 次**（第一次 AI 分析可能不完美，允许第二次修正）
4. **controller 状态追踪准确**：`le control status` 显示所有 attempt 历史与结果
5. **policy.decide_termination 被实际调用**：至少触发一次"重复失败 STOP"或"超次数 ESCALATE"的路径（可通过故意埋一个"不可修复"的 bug 验证，或限制 max-attempts=1 验证升级路径）
6. **analysis_request.json 结构完整**：失败 case 的 command/output/reason/collectors 都齐全，足以让 AI 无需额外查设备即可定位
7. 新增模块（cycle_orchestrator/analyzer_protocol/patch_applier/control_cli）有单测覆盖

### 5.10 P3 文件变更清单

| 文件 | 操作 | 说明 |
|-----|------|------|
| `engineering/loop/controller/python/loop_controller/cycle_orchestrator.py` | 新增 | 分阶段编排 |
| `engineering/loop/controller/python/loop_controller/analyzer_protocol.py` | 新增 | LlmAnalyzer ABC + 数据模型 |
| `engineering/loop/controller/python/loop_controller/patch_applier.py` | 新增 | 结构化补丁应用 |
| `engineering/loop/controller/python/loop_controller/control_cli.py` | 新增 | `le control` 子命令 |
| `engineering/loop/controller/python/loop_controller/__init__.py` | 修改 | 导出新模块 |
| `engineering/loop/controller/python/tests/test_cycle_orchestrator.py` | 新增 | 编排单测 |
| `engineering/loop/controller/python/tests/test_patch_applier.py` | 新增 | 补丁应用单测 |
| `engineering/loop/core/python/loop_core/cli.py` | 修改 | 接入 `control` 子命令 |
| `engineering/harness/scripts/apply_preset_bugs.sh` | 新增 | 预设 bug 注入脚本 |
| `engineering/loop/controller/README.md` | 修改 | controller 层文档更新 |
| `docs/specs/2026-06-22-loop-controller-sop.md` | 新增 | 主会话闭环 SOP 文档 |

**注**：P3 阶段会**临时改动 `~/workspace/` 下的 lciod 源码**（埋 bug + AI 修复），但这是闭环验证的一部分，修复后的代码应与 patchs 归档一致。演练完成后 workspace 应恢复到与 patchs 同步的干净状态。

---

## 6. 风险与缓解

### 6.1 砖机风险

| 风险 | 等级 | 缓解 |
|-----|------|------|
| vendor dd 砖机 | 🔴 | P2 不做 vendor dd，.te 改动降级 FLASH_FULL |
| boot.img dd 后启动失败 | 🔴 | sha256 校验 + 保留 rollback boot.img + 串口观察启动日志 |
| dd 错分区（手误写错 mmcblk0pN） | 🔴 | 脚本化硬编码正确节点；dd 前用 `ls -l /dev/block/mmcblk0p*` + `blkid` 二次确认 |

### 6.2 AI 分析失败

| 风险 | 等级 | 缓解 |
|-----|------|------|
| AI 分析失败（定位不准） | 🟡 | policy.decide_termination 允许最多 5 次 attempt；超限升级人工 |
| AI 改错代码导致编译失败 | 🟡 | compiler 失败立即中止，不部署；attempt 记录编译错误供下次分析 |
| AI 改对代码但引入新 bug | 🟡 | re-verify 会抓到；attempt 历史可回溯 |
| 预设 bug 间相互干扰 | 🟢 | 每次演练只埋 1 个 bug，apply_preset_bugs.sh 支持 `--bug N` 单选 |

### 6.3 编译环境

| 风险 | 等级 | 缓解 |
|-----|------|------|
| 首次 mmm 触发 soong 重建 | 🟡 | 超时设 600s；失败 fallback 到 make vendorimage |
| 网络 adb 重启后不自动连接 | 🟢 | deploy 流程末尾主动 adb connect |
| workspace 演练后残留脏改动 | 🟢 | apply_preset_bugs.sh 记录原始版本，演练后 `git checkout` 恢复 |
| 闭环中途中断（网络/设备） | 🟡 | session 状态持久化到 JSON，可 `le control status` 恢复后继续 |

---

## 7. 工程规范遵循

### 7.1 路径管理（PATH-001）

所有新脚本（`apply_preset_bugs.sh` 等）和 Python 模块（loop_deploy / loop_controller 扩展）的路径引用**全部通过 `harness-paths.conf` + `harness_env_path()` 获取**，禁止硬编码：
- `ENV_AOSP_WS` / `ENV_KERNEL_WS` / `ENV_KERNEL_OUT` / `ENV_CLANG_BIN` / `ENV_WINDOWS_IMG_DIR`
- 新增 `LOOP_DEPLOY_DIR` 到 `harness-paths.conf` 的 `PYTHON_PATH_ROOTS`

### 7.2 源码改动纪律（source-code-modify）

- 所有 lciod 源码改动（P3 演练阶段）**从 `~/workspace/` 开始**，不直接改 patchs
- 演练完成后的修复代码**归档到 patchs/**（单向归档）
- `apply_preset_bugs.sh` 只是临时注入 bug 用于验证闭环，演练后 `git checkout` 恢复

### 7.3 脚本维测（script-observability）

`engineering/` 下新增的 bash 脚本（`apply_preset_bugs.sh`）必须：
- source 公共库（`harness_bootstrap.sh`）
- 接入文件日志（`LOG_DIR` 下）
- 结构化 step + 错误现场捕获
- 统一退出码

### 7.4 文档索引一致性

改动 `engineering/loop/` 下文件后，必须检查并同步更新：
- `engineering/loop/README.md`（顶层索引）
- `engineering/loop/deploy/README.md`（新增）
- `engineering/loop/controller/README.md`（更新）
- `engineering/harness/README.md`（如有 harness 下文件变更）

---

## 8. 后续阶段（不在本次范围）

### 8.1 双设备故障注入闭环

- Pi Zero 2W（usb-fault-inject）+ Pi 5（fault-verify）双设备拓扑
- 12 类故障 F1-F12 的 loop case 设计
- loop 框架扩展支持双设备 profile（当前 `profiles/devices/rp5` 单设备）
- 时序同步：注入 → 等待 → 校验

### 8.2 LlmAnalyzer API 实现

- 接外部 LLM API（OpenAI/Claude/GLM API）
- `LlmAnalyzer` ABC 的完整实现
- controller 库内独立调用，与主会话解耦

### 8.3 vendor dd 验证 + .te 在线部署升级

- 验证 vendor.img 在线 dd（mmcblk0p6）的安全性
- 验证通过后，.te 改动从 FLASH_FULL 升级为 DD_VENDOR_REBOOT
- 整卡备份 + 砖机恢复流程标准化

### 8.4 DD_BOOT_REBOOT 内核 bug 闭环演练

- 加 1 个内核 .c 改动的预设 bug
- 演练 mode 2 编译 + dd boot.img + reboot + 复测完整闭环
- 验证内核改动场景的 AI 分析能力

---

## 附录 A: 32 能力点 → case 完整映射表

见 §3.3。所有 case 的 FQN 命名规则：`features.lciod.<suite>.<case_id>`。

## 附录 B: 10 条 DeployDecider 规则表

见 §4.3。规则按优先级从高到低匹配，命中即返回，不继续匹配。

## 附录 C: 3 个预设 bug 详细规格

见 §5.7。每个 bug 包含：位置、缺陷描述、被谁抓到、修复方式、DeployMode、定位难度。

## 附录 D: 完整闭环 SOP 脚本序列

见 §5.8。主会话按 8 步 SOP 执行，每步对应一个 CLI 命令或主会话工具调用。

---

## 参考资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `02-IO增强/README.md` + 02.01-02.05 | lciod 45 能力点定义 |
| 源码归档 | `patchs/rpi5/kernel/new/vendor/lechao/LcIod/` | 内核驱动（6 文件） |
| 源码归档 | `patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lciod/` | HAL + Daemon（24 文件） |
| 编译脚本 | `engineering/harness/scripts/mk_rpi5_full_image.sh` | mode 0-4 全覆盖（523 行） |
| 路径配置 | `engineering/harness/config/harness-paths.conf` | 单一事实源 |
| loop 框架 | `engineering/loop/` | 执行层 + 骨架层 |
| 既有 spec | `docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md` | lcview case 设计参考 |
| dd 评估 | `docs/specs/2026-06-22-lciod-loop-verification-design.md` §1.4 | rpi5 在线 dd 可行性 |

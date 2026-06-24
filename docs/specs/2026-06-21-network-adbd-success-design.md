# `network-adbd-success` 设计

> **2026-06-24 更新**：设备 IP 发现已从"固定 IP"切换为"串口动态发现"，见 `engineering/loop/scripts/rp5_serial_helper.py` 和 `engineering/loop/WORKFLOW.md` 的「传输层依赖链」章节。本文档中残留的 `192.168.1.55` 仅为历史决策记录。

> **日期**：2026-06-21
> **状态**：已确认，待实施
> **范围**：为 Loop Engineering 增加 `system.network_adbd` 场景，用于验证“重启后自动打开 WLAN → 读取 `wifi.conf` 连接 AP → 启动网络 adbd → host 侧 `adb connect 192.168.1.55:5555` 成功”这一整条链路；同时为 `loop_core` 增加最小可复用的 `host/device` 双执行平面。**不含**完整 ADB provider、DHCP 动态 IP 传值、transport 动态切换。
> **前序**：基于现有 `system.boot` / `common.shell` 场景、`rp5-serial` provider、`action: reboot` 主链，以及 RPi5 开机自动 Wi‑Fi + 网络 adbd 改动。

---

## 1. 背景

### 1.1 当前已具备的能力

Loop Engineering 当前已具备以下能力：

- `loop_core` 可加载 YAML suite、执行 case、断言结果并输出 `EvidenceBundle`
- `action: reboot` 已支持串口跨重启等待与 boot marker 判定
- `common/shell` 已提供 `shell_reachable` 与 `serial_recent / boot_log / init_log / crash_dump / kmsg` 诊断 collector
- `rp5-serial` provider 已提供稳定的串口主执行链与 early-boot transcript 能力

相关实现见：

- `engineering/loop/core/python/loop_core/cli.py`
- `engineering/loop/core/python/loop_core/executor.py`
- `engineering/loop/core/python/loop_core/collector.py`
- `engineering/loop/core/python/loop_core/runner.py`
- `engineering/loop/cases/system/boot-success.yaml`
- `engineering/loop/cases/common/shell.yaml`
- `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`

### 1.2 近期源码改动概述

RPi5 设备侧已接入“开机自动连接 Wi‑Fi + 网络 ADB”能力，链路如下：

1. `init.rpi5.rc` 在 `on boot` 阶段设置：
   - `persist.adb.tcp.port=5555`
   - `service.adb.tcp.port=5555`
2. `init.rpi5.rc` 额外 `import /vendor/etc/init/hw/init.rpi5.wifi.rc`
3. `init.rpi5.wifi.rc` 中定义 `service rpi5_wifi_connect /system/bin/rpi5_wifi_connect`
4. 该服务由 `on property:sys.boot_completed=1` 触发
5. `rpi5-wifi-connect.sh` 挂载 boot 分区到 `/data/boot`，读取 `/data/boot/wifi.conf`
6. 脚本等待 `WifiService`、显式开启 Wi‑Fi、连接目标 SSID，并维持静态 IP
7. host 侧最终通过 `adb connect <ip>:5555` 建立网络 adb

相关改动见：

- `patchs/rpi5/aosp/modified/device/brcm/rpi5/ramdisk/init.rpi5.rc.diff`
- `patchs/rpi5/aosp/new/device/brcm/rpi5/ramdisk/init.rpi5.wifi.rc`
- `patchs/rpi5/aosp/new/device/brcm/rpi5/scripts/rpi5-wifi-connect.sh`
- `patchs/rpi5/aosp/new/device/brcm/rpi5/boot/wifi.conf`
- `patchs/rpi5/aosp/modified/device/brcm/rpi5/device.mk.diff`

### 1.3 当前缺口

虽然设备侧功能已存在，但 LE 当前还缺少对这条链路的系统化验收：

1. **缺少专项 suite**：目前只有 `system.boot`，无法覆盖 Wi‑Fi 自动连接与 network adb 终态。
2. **缺少 host 执行平面**：当前 case `command` 只能发到 device transport，不能在 host 上执行 `adb connect`。
3. **缺少 network-adbd 专项 collector**：现有 collector 更偏 boot / zygote 诊断，缺少 Wi‑Fi / adbd / host adb 状态证据。
4. **不能直接依赖 ADB 作为主通道**：本场景验证目标本身就是“network adb 是否能建立”，若以 ADB 作为唯一主链，失败时会丢失早期现场。
5. **Wi‑Fi 收敛是异步的**：脚本在 `sys.boot_completed=1` 后才启动，且内部存在等待 `WifiService` 和重试逻辑，不能沿用纯快照式 case 思维。

---

## 2. 目标

1. 新增 `engineering/loop/cases/system/network-adbd-success.yaml`，形成 `system.network_adbd` 验收场景。
2. 继续以串口作为主执行与主取证通道，覆盖 reboot 到 Wi‑Fi / adbd ready 的所有 device-side 检查。
3. 为 `loop_core` 增加最小 `host/device` 双执行平面，使最终 `adb connect 192.168.1.55:5555` 可作为标准 case 执行。
4. 为本场景补齐 Wi‑Fi / adbd / host adb 的专项证据采集。
5. 最大化复用现有 `loop_core`、`rp5-serial`、`common/shell`、`boot-success` 能力，不重复造 runner / provider / case DSL。

---

## 3. 非目标

1. **不**在首版实现完整 `connection/providers/adb/` provider。
2. **不**在首版实现 DHCP 动态 IP 发现与跨 case 变量传递。
3. **不**实现 host/device 多 transport 动态切换。
4. **不**把 `adb shell true`、`ping`、`adb get-state` 等附加 smoke 纳入首版成功标准。
5. **不**修改设备侧 Wi‑Fi / adbd 源码逻辑；本轮聚焦验收链与框架补强。

---

## 4. 已确认决策

| # | 议题 | 决策 | 说明 |
|---|------|------|------|
| 1 | 验收入口 | **reboot 后完整验证** | 必须覆盖“开机自动”链路本身 |
| 2 | 终态判据 | **仅 `adb connect 192.168.1.55:5555` 成功** | 首版不加 `adb shell smoke` |
| 3 | IP 策略 | **首版固定 static IP：`192.168.1.55`** | 避免引入 DHCP 变量传递复杂度 |
| 4 | 主 transport | **继续使用串口** | 早期失败证据最完整 |
| 5 | host 能力接入方式 | **路径 A：新增 `run_on: host|device`** | 做成可复用执行平面，而不是 `adb_connect` 特判 |
| 6 | ADB provider | **首版不做** | host adb 仅作为最终判据，不作为主执行通道 |
| 7 | `wifi_service_executed` 判定 | **宽松接受 `running` 或 `stopped`** | `oneshot` rc 与脚本 daemon loop 语义存在张力 |
| 8 | collector 布局 | **network-adbd 本地 collector** | 不污染 `common/shell` 通用库 |

---

## 5. 架构设计

### 5.1 总体分层

```text
loop_core
    ├── 继续负责 case / collector 执行与 EvidenceBundle 输出
    ├── 新增 case.run_on 与 collector.run_on 语义
    └── 在 executor / collector 中新增 host 执行平面

connection.providers.rp5-serial
    ├── 继续负责 live 串口 transport
    ├── 继续负责 reboot_and_wait / serial_context
    └── 不引入 ADB provider 作为首版前置

cases/system/network-adbd-success.yaml
    ├── 复用 common/shell include
    ├── 复用 trigger_reboot + shell_reachable 风格
    └── 新增 network-adbd 本地 collectors

host subprocess
    └── 仅执行最终 adb connect 与 host collector 命令
```

### 5.2 核心原则

1. **串口仍是主执行链**：因为 network adb 本身是被验证对象，不能用它替代主执行通道。
2. **host 平面是增量补强**：只解决“host 侧动作 / 采证无法进入 LE”这一缺口。
3. **结果对象继续统一**：无论 device 还是 host 执行，最终都落到 `TestCaseResult` / `CollectorResult`。
4. **YAML 向后兼容**：现有 suite 不声明 `run_on` 时默认 `device`，不需要改动。
5. **场景定制 collector 本地化**：Wi‑Fi / adbd / host adb 证据只挂在 network-adbd suite 内，避免污染公共 collector 库。

### 5.3 为什么首版不直接做 ADB provider

不采用“先做完整 ADB provider 再做 suite”的原因：

- 当前 `rp5-serial` 已具备稳定的 reboot transcript 与 early-boot 证据能力
- network adb 建立失败的绝大多数场景都发生在 ADB 可用之前
- 现有 `boot-success` 语义明显更接近持续交互 shell + 串口 transcript，而不是一次性 `adb shell` RPC
- 首版目标是尽快打通“验收闭环”，不是先把 provider 抽象重构到最泛化状态

因此首版只把 ADB 纳入 **host 终态验证动作**，而不纳入主 transport。

---

## 6. `run_on` 扩展契约

### 6.1 case 扩展

在 case 上新增可选字段：

- `run_on: device | host`

默认值：

- `device`

语义：

- `device`：继续通过当前 transport（fixture / rp5-serial）执行
- `host`：在 host 本机通过 subprocess 执行命令

### 6.2 collector 扩展

在 collector 上新增同样字段：

- `run_on: device | host`

默认值：

- `device`

语义：

- `device`：继续由 transport 发命令并采集输出
- `host`：在 host 本机执行命令，采集 stdout/stderr/exit code 形成 evidence

### 6.3 静态校验规则

新增以下 loader 校验：

#### case 侧

1. `run_on` 仅允许 `device` / `host`
2. `action: reboot` 只能配合 `run_on: device`
3. `assert.type: prompt_visible` 只能配合 `run_on: device`
4. `run_on: host` 不允许空命令 `command: ""`

#### collector 侧

1. `run_on` 仅允许 `device` / `host`
2. `mode: serial_context` 只能配合 `run_on: device`
3. `run_on: host` 时 `commands` 不能为空

### 6.4 执行模型

#### device case

完全沿用现有逻辑：

- `transport.mark_output_boundary()`
- `transport.send_line()`
- `transport.capture_since()`
- `AssertionEngine.evaluate()`

#### host case

新增 host command runner：

- 使用本机 subprocess 执行 `bash -lc <command>`
- 合并 stdout/stderr 为 `output`
- 记录 exit code
- 继续复用现有 `AssertionEngine`
- `prompt_visible` 固定为 `False`

#### host collector

- 逐条执行 host 命令
- 结果仍组装为 `CollectorResult`
- 错误处理策略与 device collector 一致：
  - 全成功：`ok`
  - 部分失败：`degraded`
  - 全失败：`error`

---

## 7. `system.network_adbd` suite 设计

### 7.1 文件与命名

- 文件：`engineering/loop/cases/system/network-adbd-success.yaml`
- suite：`system.network_adbd`
- include：`common/shell`

### 7.2 suite defaults

建议显式设置：

- `defaults.capture_timeout`
- `defaults.recent_limit`

原因：Wi‑Fi 脚本在 `sys.boot_completed=1` 后异步启动，且内部存在最长 60s 的 `WifiService` 等待与后续连接重试；不能沿用通用的超短快照窗口。

### 7.3 case 拓扑

首版推荐 case 顺序如下：

1. `trigger_reboot`
2. `shell_reachable`（来自 `common/shell`）
3. `boot_completed`
4. `wifi_service_executed`
5. `wifi_conf_present`
6. `wifi_conf_not_default`
7. `wifi_connected_ssid`
8. `wlan_ip_ready`
9. `adb_tcp_port_persist_ready`
10. `adb_tcp_port_service_ready`
11. `adbd_running`
12. `host_adb_connect_success`

### 7.4 每条 case 的职责

#### `trigger_reboot`
- `action: reboot`
- 覆盖从重启起点开始的全量现场
- 复用 `serial_recent / init_log / crash_dump / kmsg` 失败证据

#### `boot_completed`
- 检查 `getprop sys.boot_completed` 包含 `1`
- 作为 Wi‑Fi 链路开始前的栅栏

#### `wifi_service_executed`
- 检查 `getprop init.svc.rpi5_wifi_connect`
- 宽松接受 `running` 或 `stopped`
- 用于确认 init 至少拉起过 Wi‑Fi 自动连接服务

#### `wifi_conf_present`
- 检查 `/data/boot/wifi.conf` 存在
- 等价于验证 boot 分区挂载成功且配置文件可见

#### `wifi_conf_not_default`
- 检查 `ssid` / `psk` 未落在 `default` 占位值
- 防止脚本 `exit 0` 但功能实际上未启用

#### `wifi_connected_ssid`
- 轮询 `cmd wifi status`
- 命中目标 SSID 视为成功

#### `wlan_ip_ready`
- 轮询 `ip addr show wlan0`
- 命中静态 IP `192.168.1.55` 视为成功

#### `adb_tcp_port_persist_ready`
- 检查 `getprop persist.adb.tcp.port` 包含 `5555`

#### `adb_tcp_port_service_ready`
- 检查 `getprop service.adb.tcp.port` 包含 `5555`

#### `adbd_running`
- 轮询 `getprop init.svc.adbd`
- 命中 `running` 视为成功

#### `host_adb_connect_success`
- `run_on: host`
- 执行 `adb connect 192.168.1.55:5555`
- 成功条件：exit code = 0，且输出命中 `connected to` 或 `already connected to`
- 这是首版唯一最终成功判据

### 7.5 轮询原则

由于 Wi‑Fi / adbd 状态不是瞬时成立，以下 case 不应写成“一次性查询”，而应在设备侧命令中内嵌有限时长轮询：

- `wifi_service_executed`
- `wifi_connected_ssid`
- `wlan_ip_ready`
- `adbd_running`

原则是：

- case 自己在 shell 里 `for/sleep` 轮询
- 成功时输出稳定标记（如 `SSID_OK` / `IP_READY`）
- 超时时输出末态，交给断言与 collector 诊断

---

## 8. collector 设计

### 8.1 继续复用的公共 collector

继续复用 `common/shell` 中：

- `serial_recent`
- `boot_log`
- `init_log`
- `crash_dump`
- `kmsg`

这些 collector 仍是 reboot 早期失败、shell 不稳定、network adb 不可达时最关键的证据来源。

### 8.2 network-adbd 本地 collector

#### `wifi_state`
建议收集：

- `getprop init.svc.rpi5_wifi_connect`
- `cmd wifi status`
- `ip addr show wlan0`
- `ip route`

#### `wifi_script_log`
建议收集：

- `logcat -d` 中 tag 为 `rpi5_wifi` 的日志

#### `adbd_tcp_state`
建议收集：

- `getprop persist.adb.tcp.port`
- `getprop service.adb.tcp.port`
- `getprop init.svc.adbd`

#### `host_adb_state`
- `run_on: host`
- 建议收集：
  - `adb devices`
  - `adb connect 192.168.1.55:5555`

### 8.3 推荐绑定关系

- `wifi_service_executed` 失败：`serial_recent / init_log / wifi_script_log / kmsg`
- `wifi_conf_present` / `wifi_conf_not_default` 失败：`wifi_state / wifi_script_log / serial_recent`
- `wifi_connected_ssid` / `wlan_ip_ready` 失败：`wifi_state / wifi_script_log / serial_recent / kmsg`
- `adb_tcp_port_*` / `adbd_running` 失败：`adbd_tcp_state / init_log / serial_recent`
- `host_adb_connect_success` 失败：`host_adb_state / wifi_state / wifi_script_log / adbd_tcp_state / serial_recent`

---

## 9. 错误处理与降级策略

### 9.1 host case 失败的分类

- **exit code 非 0 且断言不满足**：记为 `fail`
- **subprocess 启动失败 / 执行超时 / 运行时异常**：记为 `error`

### 9.2 host collector 降级

与 device collector 保持一致：

- 任一 host 命令抛异常，但仍有其他成功输出 → `degraded`
- 全部 host 命令失败 → `error`

### 9.3 串口优先保底

只要 `host_adb_connect_success` 失败，报告层仍然优先参考：

- `serial_recent`
- `wifi_state`
- `wifi_script_log`
- `adbd_tcp_state`

而不是仅根据 host `adb connect` stderr 做结论。

### 9.4 `wifi_service_executed` 的宽松判定

由于：

- `init.rpi5.wifi.rc` 把服务定义为 `oneshot`
- 脚本末尾却实现为 daemon loop

所以首版不强行要求 `init.svc.rpi5_wifi_connect == running`；只要能证明该服务进入过有效执行态即可。

---

## 10. 实施影响面

### 10.1 代码文件

预计修改/新增以下文件：

- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
- Modify: `engineering/loop/core/python/loop_core/executor.py`
- Modify: `engineering/loop/core/python/loop_core/collector.py`
- Create: `engineering/loop/core/python/loop_core/host_exec.py`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
- Modify: `engineering/loop/core/python/tests/test_executor.py`
- Modify: `engineering/loop/core/python/tests/test_collector.py`
- Create: `engineering/loop/core/python/tests/test_host_exec.py`
- Create: `engineering/loop/cases/system/network-adbd-success.yaml`
- Modify: `engineering/loop/README.md`

### 10.2 不需要改动的部分

首版不应修改：

- `engineering/loop/core/python/loop_core/runner.py`
- `engineering/loop/core/python/loop_core/assertion_engine.py`
- `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`
- `engineering/loop/cases/common/shell.yaml`

除非实现过程中发现测试无法覆盖的结构性缺口。

---

## 11. 验收标准

### 11.1 框架层验收

1. 旧 suite（尤其 `system.boot`）不需要声明 `run_on` 仍能保持原行为。
2. `run_on: host` case 可在 host 本机执行命令，并复用现有断言引擎给出 `pass/fail/error`。
3. `run_on: host` collector 可形成标准 `CollectorResult`。
4. `action: reboot + run_on: host`、`prompt_visible + run_on: host`、`serial_context + run_on: host` 等非法组合会在 loader 阶段 fail-fast。

### 11.2 场景层验收

在你当前静态 IP 配置下，真实设备 live 运行 `system.network_adbd` 时：

1. `trigger_reboot` PASS
2. `boot_completed` PASS
3. `wifi_service_executed` PASS
4. `wifi_conf_present` PASS
5. `wifi_conf_not_default` PASS
6. `wifi_connected_ssid` PASS
7. `wlan_ip_ready` PASS（命中 `192.168.1.55`）
8. `adb_tcp_port_persist_ready` PASS
9. `adb_tcp_port_service_ready` PASS
10. `adbd_running` PASS
11. `host_adb_connect_success` PASS

### 11.3 失败保护验收

若 Wi‑Fi / adbd / host adb 任一阶段失败，应至少产出：

- `serial_recent`
- `wifi_state`
- `wifi_script_log`
- `adbd_tcp_state`
- 若失败点在 host connect，则额外包含 `host_adb_state`

---

## 12. 推荐实施顺序

1. 先补 `run_on` schema 与静态校验
2. 再补 host command runner 与其单元测试
3. 再把 host 平面接进 executor / collector
4. 最后新增 `network-adbd-success.yaml` 与本地 collector
5. 末尾补 README 与 live 验收

---

## 13. 预期收益

1. 在不引入完整 ADB provider 的前提下，先把 network adb 场景纳入 LE 自动验收范围。
2. 保持串口作为主执行与主诊断通道，避免 network adb 失败时证据链断裂。
3. 为 LE 沉淀一个通用 `host` 执行平面，后续 host 侧 ping / fastboot / adb smoke 都能复用，而不是继续堆特判。
4. 把设备侧自动联网与 host 侧 adb 可连这两段正式打通成单场景闭环。

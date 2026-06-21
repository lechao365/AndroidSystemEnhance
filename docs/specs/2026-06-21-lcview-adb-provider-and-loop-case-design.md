# `lcview` ADB Provider + Loop Case 设计

> **日期**：2026-06-21
> **状态**：已确认，待实施计划
> **范围**：为 Loop Engineering 增加通用 `adb` provider，并基于该 provider 设计 `lcview` feature loop case；运行策略采用“serial bootstrap / fallback + adb feature run”双阶段协作。目标同时覆盖：1）通用 adb transport 能力沉淀；2）`lcview` 端到端链路验收与证据归档。**不含** HAL AIDL 扩展、物理 USB 插拔主驱动场景、全量 event_id 精确覆盖、HAL/daemon 自恢复改造。
> **前序**：基于现有 `loop_core`、`rp5-serial` provider、`system.boot`、`system.network_adbd` 场景，以及 `patchs/` 中已存在的 `lcview` kernel / HAL / daemon 实现。

---

## 1. 背景

### 1.1 `lcview` 当前真实链路

`lcview` 当前并不是简单地“抓取 logcat / kmsg”后直接输出，而是已经存在一条结构化事件链路：

```text
kernel producer
  -> /dev/vendor_lechao_lcview
  -> HAL read + batch queue
  -> AIDL getBatch()
  -> daemon validate + decode
  -> /data/vendor/lechao_lcview/logs/*.jsonl
```

关键实现位置：

- kernel 读口与 ioctl：`patchs/rpi5/kernel/new/vendor/lechao/LcView/lcview_main.c:130`
- kernel ring / stats：`patchs/rpi5/kernel/new/vendor/lechao/LcView/lcview_ring.c:350`
- HAL reader loop：`patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lcview/hal/LcView.cpp:88`
- HAL AIDL 批量接口：`patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lcview/vendor/lechao/lcview/ILcView.aidl:28`
- daemon 主循环：`patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp:126`
- daemon 写盘：`patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lcview/daemon/FileWriter.cpp:274`

因此，`lcview` loop case 的核心目标不能只停留在 `logcat` / `kmsg` 采集，而应验收 **kernel → HAL → daemon → jsonl** 的主链路是否成立，并在失败时附加 `logcat` / `kmsg` 作为辅证。

### 1.2 当前 Loop Engineering 的能力边界

Loop Engineering 已具备：

- `loop_core` 通用 case / collector / EvidenceBundle 能力
- `rp5-serial` provider 作为当前唯一 live transport
- `system.boot` 场景验证 boot 完整性
- `system.network_adbd` 场景验证“串口拿 IP + host `adb connect` 成功”

关键位置：

- transport 抽象：`engineering/loop/core/python/loop_core/transport.py:44`
- case 执行器：`engineering/loop/core/python/loop_core/executor.py:135`
- 证据输出：`engineering/loop/core/python/loop_core/evidence.py:15`
- 串口场景：`engineering/loop/cases/system/boot-success.yaml:20`
- 网络 adb 场景：`engineering/loop/cases/system/network-adbd-success.yaml:119`

当前缺口：

1. **没有通用 adb provider**：adb 目前只作为 host 侧动作，不是主 transport。
2. **`lcview` 缺少 feature suite**：现有 cases 未覆盖 `lcview` 的前提、触发、落盘、证据链。
3. **没有 adb 侧运行上下文证据**：现有 `serial_recent` 只覆盖串口第一现场。
4. **没有 bootstrap → adb feature 的标准编排**：现有 network-adbd 证明了“能 connect”，但未沉淀为可复用的二阶段模式。

### 1.3 当前 `lcview` 场景的工程约束

对 `lcview` 有三个已知事实必须进入设计：

1. **事件主来源是 USB/LcIod 路径**，不是任意 Android 用户态行为。相关事件定义见：
   - `patchs/rpi5/kernel/new/vendor/lechao/LcView/lcview_events.h:50`
   - `patchs/rpi5/kernel/new/vendor/lechao/LcIod/lciod_usbd-stats.c:500`
2. **HAL 与 daemon 都是 `oneshot` 服务**，退出后不自动重启：
   - `patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lcview/hal/lechao_lcview_hal.rc:21`
   - `patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.rc:23`
3. **daemon 在 `sys.boot_completed=1` 后才启动**：
   - `patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.rc:33`

这意味着：

- `lcview` suite 必须显式检查前提与服务状态；
- 第一版不应把精确 event_id 统计作为主成功判据；
- 失败分类必须能区分“adb 面失败 / 前提失败 / 流水线失败 / 证据失败”。

---

## 2. 目标

1. 新增通用 `adb` provider，使 `loop_core` 支持 `transport=adb` 的 live 运行模式。
2. 采用 **serial bootstrap / fallback + adb feature run** 的双阶段协作模式，最大复用已有 `system.boot` 与 `system.network_adbd` 能力。
3. 在 `engineering/loop/cases/features/lcview/` 下落地 `lcview` feature suite。
4. 为 `lcview` 补齐结构化证据采集：`jsonl`、`invalid_records.log`、`logcat`、`kmsg/dmesg`、service/property 状态。
5. 为 `lcview` run 提供结构化失败分类，避免把所有失败都粗暴归并成单一 `FAIL`。
6. 保持 `loop_core` 的场景无关性，不在 executor / case DSL 中硬编码 `lcview` 特判。

---

## 3. 非目标

1. **不**在首版实现 serial / adb 复合单 transport。
2. **不**在首版扩展 `ILcView.aidl` 暴露更多 ioctl / stats / level 能力。
3. **不**在首版实现物理 USB 插拔作为主驱动触发源。
4. **不**把全部 event_id（尤其异常类 event）出现次数做成强断言。
5. **不**把 overrun / HAL dropped batch 精确计数纳入首版成功标准。
6. **不**修改 `lcview` kernel / HAL / daemon 本身逻辑；本轮聚焦 loop 验收与 provider 沉淀。
7. **不**改造 HAL / daemon 的 `oneshot` 恢复策略。

---

## 4. 已确认决策

| # | 议题 | 决策 | 说明 |
|---|------|------|------|
| 1 | `lcview` 主目标 | **双目标并重** | 同时建设通用 adb provider 与 `lcview` loop case |
| 2 | 连接模式 | **serial + network adb** | RPi5 启动后自动联网并开启网络 adb |
| 3 | 主 transport | **adb 优先** | 能用 adb 时优先 adb，serial 仅做 bootstrap / fallback |
| 4 | bootstrap 策略 | **复用现有串口链路** | 复用 `boot-success` / `network-adbd-success` 已打通能力 |
| 5 | 触发源策略 | **adb + shell 自动化触发** | 首版不依赖外部物理动作 |
| 6 | adb provider 厚度 | **全量增强型基础能力** | shell、连接管理、pull/logcat、root/helper 一并纳入 |
| 7 | suite 目录 | **`engineering/loop/cases/features/lcview/`** | 按 feature 域组织，不塞回 `system/` |
| 8 | 成功判据 | **基础闭环验收** | 优先验证链路通、证据全、日志能落，而非全量统计 |
| 9 | serial 与 adb 协作 | **分阶段，不做复合 transport** | bootstrap / feature run / fallback 三段职责分离 |
| 10 | 失败输出 | **结构化分型** | 细分 bootstrap / adb / prereq / pipeline / evidence 等失败 |

---

## 5. 架构设计

### 5.1 总体分层

```text
serial bootstrap / fallback
    └── 复用 rp5-serial + system.network_adbd 思路
                ↓ 提供 endpoint / early evidence
loop orchestration
    └── 先 bootstrap，再切 adb feature run
                ↓
loop_core
    ├── transport factory（按 profile.transport 选择）
    ├── adb transport 执行 device case / collector
    └── EvidenceBundle 输出
                ↓
connection.providers.adb
    ├── adb connect/disconnect/wait-for-device
    ├── shell / exec-out / pull / reboot wait
    └── adb runtime context
                ↓
features/lcview suites
    ├── lcview 前提检查
    ├── 自动化触发窗口
    ├── jsonl / invalid / logcat / kmsg 采证
    └── 失败分型
```

### 5.2 核心原则

1. **adb 是 feature 主执行面**：只要网络 adb ready，后续 `lcview` 检查、拉文件、logcat、kmsg 全部走 adb。
2. **serial 只承担 bootstrap / fallback**：用于设备尚未 ready 前的引导，以及 adb 失败后的第一现场补证。
3. **provider 保持通用**：`adb` provider 不直接内置 `lcview` 业务判断；`lcview` 专用逻辑通过 feature helper / collectors 承接。
4. **loop_core 保持场景无关**：`loop_core` 只知道“当前 run 使用的 transport 是 adb”，不知道“serial + adb 混合策略”。
5. **suite 第一版坚持 YAGNI**：先做最小可验收链路，不把所有统计与异常信号绑进强断言。

### 5.3 为什么不做复合单 transport

不采用“一个 transport 内部同时持有 serial + adb，并按命令自动切换”的原因：

- 当前 `BaseTransport` 明确建模为单 transport：`engineering/loop/core/python/loop_core/transport.py:44`
- executor / collector / prompt boundary 语义都是围绕单执行通道设计的
- 把 bootstrap / endpoint 发现 / adb 切换塞进 transport，会让 provider 边界过重，且难以测试
- 对 `lcview` 这一类 feature case 来说，“阶段切换”比“运行中自动分流”更易解释、更易审计

因此本设计选择：

- **上层编排做阶段切换**
- **底层每次 run 只使用一个 transport**

---

## 6. adb provider 设计

### 6.1 目录位置与域边界

新增 provider 位置建议：

- `engineering/loop/connection/providers/adb/`

遵守现有 `connection/` 域三分层原则：

- `protocol/`：跨 provider 的协议约束（若后续需要）
- `profiles/`：设备语义
- `providers/`：具体 provider 实现

参考：`engineering/loop/connection/README.md:3`

### 6.2 provider 定位

`adb` provider 负责：

- 在已知 endpoint 的情况下建立与维持 adb 会话
- 执行设备命令、等待上线、抓取文件与日志
- 为 `loop_core` 提供统一 transport 接口
- 产出 adb 运行上下文证据

它不负责：

- 诊断设备为何没有联网
- 早期 boot transcript 采集
- `lcview` 业务判定

### 6.3 第一版能力面

#### A. 连接管理

- `adb connect <ip:port>`
- `adb disconnect <ip:port>`
- `adb devices`
- endpoint / serial 选择
- `adb wait-for-device`
- 重试 / reconnect 机制
- 连接状态探测

#### B. 命令执行

- `adb shell <cmd>`
- `adb exec-out <cmd>`
- 命令超时控制
- stdout / stderr / exit code 封装

#### C. 权限与环境

- `adb root` 封装
- root 不可用时回退 `su 0 <cmd>`
- provider 统一抽象“带 root 执行”的 helper，避免 case 重复处理

#### D. 文件能力

- `adb pull <remote> <local>`
- 目录拉取与单文件拉取
- 失败重试
- artifact 路径标准化

#### E. 调试能力

- `adb logcat -d`
- 多 buffer 采集（如 `main/system/crash`）
- 属性采集：`getprop`
- service 状态采集：`init.svc.*`
- 进程采集：`ps -A`
- 文件 / 目录检查：`ls` / `stat` / `test`

### 6.4 与 `BaseTransport` 的适配

对 `loop_core` 暴露的仍然是统一 transport 语义：

- `acquire_writer`
- `release`
- `send_line`
- `mark_output_boundary`
- `capture_since`
- `reboot_and_wait`

但 adb transport 的内部实现不再是串口 transcript 模型，而是 **命令式 shell 执行模型**：

- `send_line` 提交一条 shell 命令
- `capture_since` 返回该命令对应输出与 prompt / exit code 结果
- `reboot_and_wait` 走 `adb reboot` + `wait-for-device` + `boot_completed` 检查链

### 6.5 adb runtime context

为与现有 `serial_context` 对齐，adb provider 需要补充运行上下文，建议至少包含：

- `adb_endpoint`
- `adb_device_serial`
- `adb_recent_commands`
- `adb_reconnect_count`
- `adb_wait_for_device_result`
- `adb_logcat_snapshot_path`

这些信息将进入 EvidenceBundle / summary，帮助区分“adb 面失败”和“业务失败”。

### 6.6 `lcview` helper 的边界

`lcview` 第一版确实需要一批 adb 调试能力，但这些 helper 不应写进 transport 主类。建议单独以 feature helper / collector 形式承接，例如：

- 清理 `/data/vendor/lechao_lcview/logs`
- 拉取 `logs/*.jsonl`
- 拉取 `invalid_records.log`
- 抓取 `lechao_lcview` / `lechao_lcview_hal` logcat
- 抓取 `dmesg | grep -i lcview`
- 抓取 `getprop init.svc.*` / `ps -A` 中的 `lcview` 状态

这样可以保证：

- provider 对其他 feature 场景仍然通用
- `lcview` 的 feature 逻辑集中在 case / collector 层，而非分散到 transport 内部

---

## 7. serial bootstrap / fallback 设计

### 7.1 协作边界

serial 与 adb 的协作采用三段式：

1. **bootstrap 阶段**：复用串口能力确认设备完成 boot、联网、可 `adb connect`
2. **feature run 阶段**：切换到 adb provider 执行 `lcview` suite
3. **fallback 阶段**：如果 adb run 失败，则附加串口第一现场作为补证

### 7.2 bootstrap 的职责

bootstrap 的目标不是执行 `lcview` 业务逻辑，而是提供：

- early-boot 可观察性
- `wlan0` IP / adb endpoint
- network adb ready 证明

这部分最大化复用：

- `engineering/loop/cases/system/boot-success.yaml`
- `engineering/loop/cases/system/network-adbd-success.yaml`

### 7.3 推荐编排方式

#### 方式 A：两次 run

- run #1：bootstrap（串口）
- run #2：`lcview` feature（adb）

#### 方式 B：上层 workflow 单入口（推荐）

- 对外表现为一个 `lcview` 验收入口
- 内部先跑 bootstrap，再抽 endpoint，再跑 adb suite，再汇总 evidence

推荐方式 B，因为它同时满足：

- 不扭曲 `loop_core` 的单 transport 语义
- 保持用户体验为“一次业务入口”
- 便于后续复用到其他依赖 network adb 的 feature suite

### 7.4 fallback 分级

当 adb feature run 失败时，fallback 按三级执行：

1. **adb 补证**：`adb devices` / reconnect / wait-for-device / 再拉一轮 logcat 与状态
2. **serial 补证**：`serial_recent` / reboot marker / panic marker / 最近片段
3. **结构化结论**：明确失败位于 bootstrap、adb connect、adb exec、pipeline 还是 evidence

---

## 8. `lcview` suite 设计

### 8.1 目录与文件组织

按 feature 域落位：

- `engineering/loop/cases/features/lcview/`

推荐首版拆分：

- `common.yaml`：公共原子检查与公共 collectors
- `end_to_end.yaml`：主流程 suite

后续如需扩展，可继续新增：

- `stress.yaml`
- `rotation.yaml`
- `service-recovery.yaml`

### 8.2 首版目标

首版只做 **基础闭环验收 + 关键证据完整性**，不追求一口气覆盖全部统计与异常场景。

主目标：

1. adb 通道可用
2. `lcview` 前提成立
3. 清理旧日志并形成一轮新触发窗口
4. 生成新的 `jsonl`
5. 成功 pull 回关键文件与日志
6. 输出结构化失败分类

### 8.3 主流程编排

推荐主流程如下：

#### Phase 0：前置依赖

- `adb_shell_reachable`
- `boot_completed`
- `lcview_services_running`
- `lcview_schema_present`
- `lcview_data_dir_ready`

#### Phase 1：环境清理与 pre-state

- `lcview_cleanup_old_logs`
- `lcview_capture_pre_state`

pre-state 建议采集：

- 日志目录快照
- `getprop init.svc.*lcview*`
- `ps -A | grep lcview`
- `logcat -d -s lechao_lcview:V lechao_lcview_hal:V`
- `dmesg | grep -i lcview`

#### Phase 2：自动化触发窗口

- `lcview_trigger_event_window`

由于当前已知 `lcview` 事件主要来自 USB/LcIod，第一版不把某个特定异常事件作为强依赖；而是通过一组 **稳定、可重复、侵入性低** 的 adb shell 动作，促使 `lcview` 主链路产生至少一轮新记录。

#### Phase 3：结果验证

- `lcview_jsonl_generated`
- `lcview_jsonl_non_empty`
- `lcview_invalid_log_clean_or_bounded`
- `lcview_logcat_no_fatal_breakage`
- `lcview_pull_evidence_success`

### 8.4 公共 collectors

建议至少提供以下 `lcview` collectors：

- `lcview_logcat`
- `lcview_kmsg`
- `lcview_service_state`
- `lcview_files`
- `lcview_pull_logs`
- `lcview_invalid_log`
- `serial_recent`（兜底）

其中：

- `lcview_logcat` 聚焦 `lechao_lcview` / `lechao_lcview_hal`
- `lcview_kmsg` 聚焦 `dmesg` 中 `lcview` 相关内容
- `lcview_files` 用于列目录、尺寸、mtime、前后快照
- `lcview_pull_logs` 负责把结构化日志带回 host

### 8.5 不进入首版强断言的内容

以下内容保留为后续扩展，不纳入首版 PASS 条件：

- 精确统计每个 event_id 出现次数
- overrun 计数增量严格比对
- HAL dropped batch 定量分析
- schema 字段语义与 producer 逐字段强校验
- HAL / daemon 退出后的恢复性场景

原因：

- 当前 HAL AIDL 观测面有限，`ILcView.aidl` 只暴露 `getBatch/getOverrunCount`
- 当前 `lcview_events.json` 与部分 producer 代码在字段命名语义上存在偏差，首版若做强绑定会导致 suite 过脆

---

## 9. 成功判据与断言模型

### 9.1 三层断言

#### A. 连接层断言

验证 adb 执行面成立：

- `adb shell` 可达
- endpoint / serial 与预期一致
- root / `su 0` 至少足以访问 `lcview` 日志目录

#### B. 服务层断言

验证 `lcview` 链路前提成立：

- HAL / daemon 状态合理
- schema 文件存在
- 数据目录可访问

#### C. 业务层断言

验证本轮 `lcview` 触发与落盘成立：

- 存在新的 `*.jsonl`
- 新文件非空
- 能成功 pull 回 host
- `invalid_records.log` 无明显异常膨胀
- logcat / kmsg 中没有明确的链路断裂信号

### 9.2 强断言与弱断言

#### 强断言（直接 FAIL）

- adb shell 不可用
- `lcview` 前提不满足
- 触发后没有新 `jsonl`
- 新 `jsonl` 为 0 字节
- 关键 evidence pull 失败
- logcat 明确显示 HAL / daemon / schema / device open 失败

#### 弱断言（warning / evidence）

- `invalid_records.log` 有少量内容
- logcat 存在零星非关键 error
- dmesg 有历史噪声
- 日志目录存在旧轮转痕迹

这种分层是为了避免首版 suite 因历史噪声过脆。

---

## 10. 证据模型

### 10.1 结构化文件证据

至少采集：

- `logs/*.jsonl`
- `invalid_records.log`
- 触发前后目录快照
- 新文件列表 / 文件大小 / mtime 摘要

### 10.2 用户态证据

至少采集：

- `logcat`：`lechao_lcview`
- `logcat`：`lechao_lcview_hal`
- `getprop init.svc.*lcview*`
- `ps -A | grep lcview`

### 10.3 内核态证据

至少采集：

- `dmesg | grep -i lcview`
- 必要时更大窗口的 `kmsg` / `dmesg` 片段

### 10.4 adb runtime context

作为 EvidenceBundle 的补充上下文，至少包含：

- endpoint
- device serial
- recent commands
- reconnect count
- logcat snapshot path

### 10.5 serial fallback context

当 adb run 失败时，附加：

- `serial_recent`
- reboot marker / panic marker 邻近片段
- transcript 路径

---

## 11. 失败分类

最终结果不应只输出笼统 `FAIL`，而应分型：

1. `BOOTSTRAP_FAIL`
2. `ADB_CONNECT_FAIL`
3. `ADB_EXEC_FAIL`
4. `LCVIEW_PREREQ_FAIL`
5. `LCVIEW_TRIGGER_FAIL`
6. `LCVIEW_PIPELINE_FAIL`
7. `LCVIEW_EVIDENCE_FAIL`

每一类失败都需要有对应的最小关键信息，例如：

- 当前 endpoint
- 服务状态
- 触发前后新增文件数
- 最新 `jsonl` 文件名与大小
- invalid log 状态
- 核心 logcat / kmsg 摘要

这样后续无论人工排障还是 `/le` 自动诊断，都能快速接手。

---

## 12. 实施边界与顺序

### 12.1 首版必须交付

1. 通用 adb provider
2. `transport factory` 支持 `transport=adb`
3. `lcview` common + end_to_end suite
4. bootstrap → adb feature 的上层编排
5. adb provider 基础测试 + 真机集成验证

### 12.2 首版明确不做

1. serial / adb 复合单 transport
2. 全量 event_id 精确覆盖
3. overrun / dropped batch 精确定量验收
4. AIDL 扩展
5. HAL / daemon 自恢复改造
6. 物理 USB 插拔主流程
7. schema 语义纠偏修复

### 12.3 推荐实施顺序

1. `loop_core` 引入 transport factory，按 `DeviceProfile.transport` 选择 provider
2. 落 adb provider 最小执行面
3. 补 pull / logcat / runtime context
4. 跑通一个最小 adb shell suite
5. 落 `lcview` common + end_to_end
6. 串 bootstrap + feature workflow

---

## 13. 风险与后续扩展

### 13.1 首版主要风险

1. **触发窗口稳定性不足**：若 adb shell 触发动作无法稳定促发 `lcview` 记录，需要进一步收敛到更贴近 USB/LcIod 的动作设计。
2. **oneshot 服务可用性脆弱**：HAL / daemon 退出后不会自动恢复，suite 只能做发现与分类，不能自动自愈。
3. **字段语义偏差**：`lcview_events.json` 与部分 producer 字段命名不完全一致，不适合首版做深度语义断言。
4. **adb transport 语义差异**：串口的 prompt / transcript 模型与 adb 的命令式 RPC 模型不同，provider 适配需控制复杂度。

### 13.2 后续扩展方向

1. 针对 `lcview` 增加 stress / rotation / service-recovery 场景
2. 增加 event-level 统计或 schema-level 语义校验
3. 补充 overrun / HAL queue pressure 观测能力
4. 视需要评估是否扩展 `ILcView.aidl` 暴露更强 stats / control 面
5. 将 bootstrap → adb feature 的编排模式抽象成可复用 workflow，服务更多 network-adb feature 场景

---

## 14. 结论

最终推荐方案为：

- **架构采用“通用 adb provider + serial bootstrap/fallback”**
- **执行采用“先 bootstrap，再 adb feature run”双阶段模型**
- **`lcview` 第一版定位为“基础闭环验收 + 关键证据完整性”**
- **不在首版引入过深统计与恢复逻辑，优先把 transport、suite、evidence、failure taxonomy 打牢**

该方案最大化复用现有 `loop_core`、`rp5-serial`、`system.boot`、`system.network_adbd` 资产，并为后续 Android feature 级 network-adb 验收建立统一底座。

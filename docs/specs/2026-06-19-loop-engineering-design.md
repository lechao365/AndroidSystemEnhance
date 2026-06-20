# loop engineering 与 rp5-serial 接管方案设计

> **日期**：2026-06-19
> **状态**：已确认，待实施
> **范围**：在 `engineering/loop/` 下建立 loop engineering 框架；首期实现 `connection/providers/rp5-serial/`，支持 Windows 11 宿主机常驻 Host + WSL2 Client 的树莓派 5 串口接管；首个业务 workflow 为启动失败 / 反复重启场景下的 `boot-failure-debug-loop` v1。

---

## 1. 背景与目标

### 1.1 现状

当前开发环境为：

- 宿主机：Windows 11
- 开发环境：WSL2
- 目标设备：树莓派 5
- 当前串口使用方式：Windows 侧 GUI 工具（如 MobaXterm）手工查看串口日志

当前痛点不是“如何临时打开串口”，而是：

1. `~/workspace/` 编译出的镜像刷机后，系统存在**启动失败**、**无网络**、**无 adbd**、**反复重启**等问题。
2. 一旦系统未完成启动，ADB/SSH 等上层通道不可用，只剩串口可作为接管通道。
3. 后续计划在 `engineering/loop/` 下建立完整的 **loop engineering** 能力，由 AI 接管开发板，执行“观察 → 分类 → 采样 → 诊断 → 报告”的闭环。
4. 该闭环必须先具备稳定的串口接管基础设施，否则后续自动化调试无法落地。

### 1.2 核心问题

WSL2 并不适合作为物理串口的直接唯一拥有者：

- 物理串口首先存在于 Windows COM 设备语义中。
- 直接把串口透传给 WSL2，会引入宿主机/驱动/透传/抢占等稳定性问题。
- 单纯把串口映射为日志文件，又无法满足人工交互与自动化命令发送的需求。

因此，系统必须同时满足：

1. Windows 侧稳定独占 COM 口。
2. WSL2 中能够像 MobaXterm 一样查看日志并输入命令。
3. 同一时刻只允许一个写入者，避免人工与自动化互相污染现场。
4. 串口原始数据、调试动作、诊断结果都可归档与回溯。
5. 连接层与 workflow / rules / report 层解耦，后续可扩展 ADB。

### 1.3 目标

本设计的目标不是做“串口小工具”，而是建立首个可扩展的 loop engineering 基础：

1. 在 `engineering/loop/` 下建立清晰的框架层次：`core / connection / workflows / profiles`。
2. 在 `connection/providers/rp5-serial/` 下同仓管理：
   - Windows Host
   - WSL2 Client
   - 协议与共享模型
   - provider 测试与 profile
3. 实现三种使用模式：
   - `monitor`：只读观察
   - `interactive`：人工独占写入
   - `automation`：workflow 独占写入
4. 首期实现 `boot-failure-debug-loop` v1，面向：
   - 无输出
   - kernel panic
   - boot hang
   - login prompt 不可达
   - 反复重启
5. 首期只做 **L1/L2** 动作：
   - L1：只读采样
   - L2：低风险探测
   - 暂不做 L3/L4 激进恢复

---

## 2. 设计边界与非目标

### 2.1 已确认边界

本次设计已确认以下边界：

1. **`engineering/loop/` 只承载 loop engineering 自身内容**，不承载对 `engineering/harness/` 的重构。
2. **`engineering/harness/` 保持原位**：
   - `lib/`
   - `log/`
   - `rules/script-observability.md`
   - 现有 workflows/scripts/config/templates
   均不迁移。
3. loop 中的 **bash workflow 入口脚本** 若需要 observability，继续复用：
   - `engineering/harness/lib/harness_bootstrap.sh`
   - `engineering/harness/lib/harness_observability.sh`
   - `engineering/harness/rules/script-observability.md`
4. Windows Host 与 WSL2 Client / protocol / shared **同仓管理**，但 **运行位置不同**。
5. Windows Host 只需要**轻量维测能力**，不强制套用 harness 的完整脚本维测框架。
6. 同一时刻只允许 **一个写入者**；人工和 automation 可同时观察，但不能同时发命令。

### 2.2 非目标

以下内容明确**不属于 V1 范围**：

1. ADB provider 的实现。
2. serial → adb 自动切换实现。
3. 多设备并行调度。
4. 激进恢复动作（L3/L4）：
   - 修改系统文件
   - 持久化配置写入
   - 破坏性修复
5. 图形界面。
6. 大而全的事件总线平台化。

---

## 3. 总体架构

### 3.1 分层结构

建议 `engineering/loop/` 按以下层次组织：

```text
engineering/loop/
├── README.md
├── WORKFLOW.md
├── core/
├── connection/
│   ├── README.md
│   ├── protocol/
│   ├── profiles/
│   └── providers/
│       └── rp5-serial/
├── workflows/
│   └── boot-failure-debug-loop/
└── profiles/
```

分层职责：

- **core/**：loop 的通用抽象，不绑定具体连接器。
- **connection/**：连接域定义、协议、provider profile、具体 provider 实现。
- **workflows/**：面向业务场景的闭环流程。
- **profiles/**：设备级/场景级配置。

### 3.2 rp5-serial provider 结构

`connection/providers/rp5-serial/` 建议采用同仓管理结构：

```text
engineering/loop/connection/providers/rp5-serial/
├── README.md
├── WORKFLOW.md
├── host/
│   ├── README.md
│   ├── runtime/
│   ├── logging/
│   └── packaging/
├── client/
│   ├── README.md
│   ├── monitor/
│   ├── interactive/
│   └── automation/
├── shared/
│   ├── README.md
│   ├── models/
│   └── codec/
└── tests/
```

职责：

- **host/**：Windows 常驻串口宿主进程。
- **client/**：WSL2 monitor / interactive / automation 入口。
- **shared/**：host/client 共享协议模型与编解码。
- **tests/**：协议、状态机、集成测试。

### 3.3 核心拓扑

```text
RPi5 UART
  -> Windows COM
  -> rp5-serial host
     -> raw stream / host local log / transcript
     -> session state / writer lease
     -> protocol endpoint
  -> WSL2 rp5-serial client
     -> monitor
     -> interactive
     -> automation
  -> boot-failure-debug-loop
     -> rule engine
     -> collectors
     -> reports
```

设计原则：

1. **物理串口只由 Windows Host 独占**。
2. **WSL2 侧不直接打开物理串口**，而是通过逻辑会话访问 Host。
3. **Host 不负责故障判定**，只负责串口托管、数据转发、轻量维测。
4. **故障规则、采样动作、报告生成全部在 WSL2 workflow 侧实现**。

---

## 4. harness 与 loop 的关系

### 4.1 保持 harness 不变

本设计明确不重构 `engineering/harness/`。原因：

1. `harness` 已经有稳定语义：规则、workflow、模板、脚本维测承载层。
2. 若把 `log/lib/rules` 上移到 `engineering/` 根，会造成大范围路径与文档改动。
3. 当前主目标是先把 loop engineering 跑通，而不是先做基础设施迁移。

### 4.2 loop 对 harness 的允许依赖

loop 中的 **bash 入口脚本** 允许依赖：

- `engineering/harness/lib/harness_bootstrap.sh`
- `engineering/harness/lib/harness_observability.sh`
- `engineering/harness/rules/script-observability.md`

但 loop **不应依赖** harness 的业务 workflow 逻辑，不应把 patchs/workspace 的业务规则耦合进 loop 核心。

### 4.3 日志与 artifact 落点

日志分为两类：

#### A. WSL2 bash workflow 日志

继续落到：

```text
engineering/output/log/<script-name>/
```

为避免与现有 harness 脚本混淆，loop 脚本统一采用前缀命名：

- `loop-rp5-serial-monitor`
- `loop-rp5-serial-interactive`
- `loop-rp5-serial-automation`
- `loop-boot-failure-debug`

#### B. Windows Host 本地轻量日志

由 Host 自己维护，不强制走 harness log。其作用：

- Host 启停诊断
- 串口打开失败原因
- reconnect 记录
- attach/detach 记录
- writer acquire/release 记录
- 异常退出原因

必要时，由 WSL2 workflow 把 Host 关键日志片段归档到本次 attempt 的 artifacts 中。

---

## 5. 配置模型

### 5.1 混合配置模式

本设计采用已确认的 **混合模式**：

#### Host 最小运行配置

仅描述“服务如何跑起来”：

- COM 标识
- baudrate
- listen address / port
- reconnect 策略
- host 本地日志路径/轮转基础参数

#### Loop 语义配置

由 `connection/profiles/` 与 `loop/profiles/` 提供，描述“如何理解这台板子”：

- prompt marker
- boot marker
- line ending
- boot timeout
- reboot loop 阈值
- rule 参数
- workflow override

### 5.2 配置优先级

建议按以下顺序覆盖：

1. provider 默认配置
2. 设备 profile（RPi5）
3. workflow override

这样可以做到：

- Host 不依赖完整 workflow 才能运行
- workflow 又能在场景级别覆写阈值和采样策略

---

## 6. 核心数据模型

### 6.1 Session

表示一次 provider 会话：

- `session_id`
- `device_id`
- `transport`（固定为 `serial`）
- `mode`（`monitor | interactive | automation`）
- `writer_owner`
- `started_at`
- `ended_at`
- `profile_id`
- `artifacts_ref`
- `transcript_ref`

作用：

- 追踪一次串口接管的生命周期
- 区分读模式与写模式
- 关联 transcript 与 artifacts

### 6.2 WriterLease

表示当前写入权：

- `lease_id`
- `session_id`
- `owner_type`（`human | workflow`）
- `owner_id`
- `mode`
- `acquired_at`
- `expires_at`
- `state`

作用：

- 保证同一时刻只有一个写入者
- 支持 TTL/失联回收
- 清晰记录是谁在控制板子

### 6.3 StreamEvent

统一输入/输出事件：

- `ts`
- `session_id`
- `seq`
- `direction`（`in | out | meta`）
- `source`（`device | human | agent | host`）
- `channel`（`serial`）
- `payload_text`
- `payload_raw_ref`
- `tags`

用途：

- 设备输出
- 人工输入
- automation 输入
- Host 元事件（lease 变化、attach/detach）

### 6.4 RuleMatch

表示一条规则命中结果：

- `rule_id`
- `session_id`
- `matched`
- `confidence`
- `severity`
- `evidence`
- `phase`
- `suggested_actions`

### 6.5 ActionRecord

表示 workflow 执行动作：

- `action_id`
- `session_id`
- `attempt_id`
- `level`（V1 仅使用 `L1`/`L2`）
- `command`
- `reason`
- `started_at`
- `finished_at`
- `result`
- `evidence_ref`

### 6.6 LoopAttempt

表示一次完整调试闭环：

- `attempt_id`
- `device_id`
- `trigger`
- `start_at`
- `end_at`
- `matched_rules`
- `actions`
- `outcome`
- `final_classification`
- `report_ref`

---

## 7. Host / Client 协议与运行模型

### 7.1 协议目标

协议只解决三件事：

1. 建立与查询 session
2. 订阅输出流
3. 在写入权受控的前提下发送输入

### 7.2 最小命令集

V1 需要支持：

- `session.open`
- `session.close`
- `session.status`
- `stream.subscribe`
- `writer.acquire`
- `writer.release`
- `input.send`
- `input.send_line`
- `expect.wait`

### 7.3 Host 职责边界

Windows Host 只负责：

1. 串口打开与重连
2. 数据读写
3. transcript / raw stream 记录
4. session 状态维护
5. writer lease 管理
6. 对外提供协议端点
7. Host 自身轻量维测

Windows Host **不负责**：

- 启动失败分类
- panic 识别
- reboot loop 识别
- 规则引擎
- 自动恢复策略

### 7.4 Client 三模式

#### monitor

- 只读观察
- 可多人并发观察
- 不允许写入

#### interactive

- 人工独占写入
- 在 WSL2 中提供近似 MobaXterm 的交互体验

#### automation

- workflow 独占写入
- 用于 `boot-failure-debug-loop` 等自动化流程

### 7.5 冲突处理

- 读通道允许共享
- 写通道必须独占
- 若已有 writer，占用者之外的新写请求应被拒绝或显式等待

---

## 8. Windows Host 设计

### 8.1 技术建议

V1 推荐采用：

- 语言：Python
- 串口库：`pyserial`
- 常驻方式：由 Windows service wrapper（如 NSSM / WinSW）托管

理由：

- 串口收发成熟
- host/client/shared 协议建模方便
- 后续扩展 ADB provider 时更易统一

### 8.2 Host 运行状态

Host 至少应区分以下状态：

- `STARTING`
- `IDLE`（等待 client）
- `SERIAL_CONNECTED`
- `SERIAL_DEGRADED`（串口掉线/重连中）
- `STOPPING`
- `FAILED`

### 8.3 Host 轻量维测

Host 必须具备自己的运行日志，但不必遵循 harness 的完整脚本维测规范。至少记录：

- 启动/停止
- 配置加载结果
- 串口打开成功/失败
- reconnect
- client attach/detach
- writer acquire/release
- 未处理异常

### 8.4 Host 产物

Host 侧至少提供：

- 本地轻量运行日志
- 原始串口流保存
- transcript 生成基础能力
- session 状态输出
- health check 输出

---

## 9. WSL2 Client 设计

### 9.1 设计目标

WSL2 Client 必须既服务人工，也服务 automation。其目标是：

1. 你能像在 MobaXterm 中一样实时看日志并输入命令。
2. workflow 不依赖模拟键盘，而是调用稳定的自动化接口。

### 9.2 Monitor 模式

用途：

- 启动日志观察
- 正常系统日志观察
- automation 旁路观察

要求：

- 实时输出
- 不持有 writer lease
- 可安全退出/重连

### 9.3 Interactive 模式

用途：

- 人工串口交互
- 查询 shell、`logcat`、`dmesg`

要求：

- 获取 writer lease
- 实时回显输出
- 支持 detach
- 若 writer 已被 workflow 占有，则拒绝或显式等待

### 9.4 Automation 模式

用途：

- 给 `boot-failure-debug-loop` 提供编排接口

要求：

- `send_line`
- `wait_for_pattern`
- `capture_window`
- 可设置 timeout / retry
- 不依赖人机交互模拟

---

## 10. boot-failure-debug-loop v1

### 10.1 业务目标

面对以下场景时，workflow 自动接管串口并给出诊断结果：

- 无输出
- kernel panic
- boot hang
- login prompt 不可达
- 反复重启

### 10.2 状态机

V1 建议状态：

- `PREPARE`
- `ATTACH_SERIAL`
- `OBSERVE_BOOT`
- `CLASSIFY_FAILURE`
- `COLLECT_EVIDENCE`
- `REASSESS`
- `EXIT_SUCCESS`
- `EXIT_FAILURE`

### 10.3 Boot cycle 检测

为支持“反复重启”分析，必须引入 `boot_cycle_id` 概念，用于：

- 识别 boot 起点
- 标记 reboot 边界
- 按 cycle 归档关键错误片段
- 区分“系统自己重启”与“调试动作后重启”

### 10.4 V1 规则集

首批规则建议：

- `no_output_after_attach`
- `kernel_panic_detected`
- `kernel_boot_hang`
- `login_prompt_not_reached`
- `shell_prompt_available`
- `reboot_loop_detected`

V1 规则以：

- 文本特征
- 时间窗口
- 阶段推进失败

为主，不引入复杂 DSL。

### 10.5 V1 动作边界

V1 仅允许：

#### L1：只读采样

- `dmesg`
- `logcat`
- `getprop`
- `mount`
- `ps`
- 抓最近 N 行上下文
- 抓某时间窗口输出

#### L2：低风险探测

- 发送回车
- 等待 prompt
- 温和重试只读命令
- 等待更长观察窗口

V1 明确**不做**：

- L3 恢复动作
- L4 高风险动作

原因：首期目标是先把“接管、采样、分类、报告”链路跑通，避免过早引入现场污染风险。

### 10.6 V1 报告输出

每轮 attempt 至少输出：

- 最终分类
- 启动推进阶段
- boot cycle 次数
- 命中规则
- 执行动作
- 关键证据
- 建议下一步

报告需同时支持：

- 人类可读格式
- 机器可读格式

---

## 11. 验证策略

### 11.1 分层验证

V1 不应把所有验证都压到真机上。建议分层：

1. **shared 层测试**：协议模型、编解码、错误码。
2. **host 测试**：串口主循环、lease、轻量日志。
3. **client 测试**：monitor / interactive / automation 行为。
4. **host-client integration**：协议与订阅/写入闭环。
5. **board 测试**：树莓派 5 真实启动/失败场景。

### 11.2 真机验证场景

至少验证：

1. 正常启动场景：
   - 能看日志
   - 能交互 shell
2. 启动失败场景：
   - 能接管
   - 能采样
   - 能报告
3. 反复重启场景：
   - 能识别 boot cycle
   - 能给出 reboot loop 分类

---

## 12. V1 / V2 分界

### 12.1 V1 交付内容

1. `engineering/loop/` 骨架
2. `connection/providers/rp5-serial/` 同仓结构
3. Windows Host MVP
4. WSL2 Client MVP
5. `monitor / interactive / automation` 三模式
6. 单 writer lease
7. transcript / raw stream / report 基础产物
8. `boot-failure-debug-loop` v1
9. 基础规则 + L1/L2 动作 + 报告

### 12.2 V2 预留

1. L3 恢复动作
2. ADB provider
3. serial → adb 切换
4. 更多规则与更复杂策略
5. 多设备支持

---

## 13. 推荐实施顺序

### 第一批：冻结结构与契约

1. 边界冻结
2. 目录结构与职责骨架
3. 配置模型
4. `core` 数据模型
5. host/client/shared 协议

### 第二批：打通串口基础链路

1. Windows Host MVP
2. WSL2 Client MVP
3. writer lease
4. transcript / raw / status

### 第三批：打通启动失败闭环

1. 状态机
2. boot cycle 检测
3. V1 规则集
4. L1/L2 采样动作
5. report 输出

### 第四批：验证收敛

1. 分层测试
2. 真机验证
3. 失败场景验证

---

## 14. 结论

本设计最终收口为：

1. **不迁移 `engineering/harness/`**，loop 仅复用其 bash 维测基础设施。
2. **在 `engineering/loop/connection/providers/rp5-serial/` 同仓管理 Host / Client / Shared / Tests。**
3. **Windows Host 独占 COM，WSL2 作为人工与 AI 的主入口。**
4. **读共享、写独占；人工与 automation 通过 writer lease 隔离。**
5. **V1 优先完成“稳定接管 + 稳定采样 + 稳定分类 + 稳定报告”。**
6. **更积极恢复动作与 ADB 扩展留到 V2。**

# Loop Engineering v2：用例驱动 + AI 自动修复闭环

> **日期**：2026-06-19
> **状态**：已确认
> **范围**：将 loop engineering 从"规则盲匹配引擎"重构为"用例驱动验收器 + AI 自动修复闭环"。LE 框架（loop_core）负责确定性断言执行，AI（opencode driver）负责用例生成、根因分析、代码修复。完全移除 workflows/ 层，新场景零 Python 代码（仅写 YAML 用例）。
> **前序**：基于 `2026-06-19-loop-core-extraction-design.md`（core 抽取已完成）和 `2026-06-19-loop-engineering-design.md`（v1 架构）。

---

## 1. 背景

### 1.1 当前架构的根本问题

当前 LE 架构采用"规则盲匹配"模式：

```
采集 → 规则引擎(marker in text) → 分类 → 固定 L1 动作 → 报告
```

这套模式有三个根本缺陷：

1. **匹配率低**：文本 marker 匹配无法理解语义。如 zygote 反复重启，规则只能匹配 `init: starting service 'zygote'` 文本，无法判断"重启频率是否异常"。
2. **规则与 AI 职责重叠**：规则试图同时做"判断系统状态"和"诊断根因"，两头不讨好。前者需要确定性，后者需要语义理解。
3. **扩展成本高**：每加一个诊断维度都要写 Python 规则类 + 测试 + 状态机分支，且规则间相互干扰（优先级冲突）。

### 1.2 核心洞察

用户提出的架构重构核心思想：

> **确定性的事用断言（验收用例），非确定性的事用 AI（大模型语义分析）。规则引擎两头不讨好，应该拆掉。**

具体到两个层次：

**模块级 LE**：编码完成后，AI 读取模块代码 + 需求文档，遵循模板生成验收用例 → LE 框架执行用例 → fail 时 AI 分析证据并修改代码 → 重新编译上板测试 → 循环直到全 pass。

**系统级 LE**：AI 组合 common 原子用例 + 模块用例生成系统级场景 → LE 执行 → fail 时 AI 分析修复 → 循环。

### 1.3 与工业界模式对照

本架构本质是 **AI 驱动的 VTS（Vendor Test Suite）+ 自动修复闭环**：
- Android VTS/CTS：厂商测试套件，确定性断言
- Google Test Kit：自动化测试 + 报告
- 本架构的增量创新：**AI 生成用例 + AI 自动修复**，形成 closed-loop

---

## 2. 目标

1. 将 `loop_core/rules.py`（规则引擎）替换为 `assertion_engine.py`（断言引擎），实现确定性用例执行
2. 引入声明式用例格式（YAML），支持 `include`（用例复用）和 `requires`（依赖声明）
3. 引入 `EvidenceBundle` JSON 格式，作为 LE 框架与 AI 之间的证据契约
4. **完全移除 workflows/ 层**，通用 `LoopRunner` 上提到 core，新场景零 Python 代码
5. 引入 `templates/case-template.md`，约束 AI 生成用例的格式与质量
6. 提供 LE CLI 工具集（`le run` / `le gen-cases` / `le deploy`），供 opencode driver 调用
7. 全程自动化测试覆盖，联合回归全绿
8. **实施修改时所有 README.md 必须重新生成**

---

## 3. 非目标

1. **不实现内嵌 LLM 集成**：AI 驱动由 opencode 本身承担，LE 框架不调用 LLM API
2. **不实现全自动镜像部署**：镜像刷写需用户确认（binary 替换可自动）
3. **不实现用例版本与代码版本的自动绑定**：用例 git 管理即可，不做自动关联
4. **不删除 transport/config/observer/cycles**：这些通用层保留
5. **不重构 harness**：LE 复用现有 harness observability
6. **不修改 docs/ 下的历史归档 spec/plan**：历史文档不动

---

## 4. 已确认决策清单

| # | 议题 | 决策 | 说明 |
|---|---|---|---|
| 1 | 架构模式 | 用例驱动 + AI 分析 | 确定性断言替代规则盲匹配；opencode 作为 driver |
| 2 | 用例粒度 | 两层分类 | 大类（CRASH/STABILITY/PERFORMANCE/SECURITY/FEATURES）+ 子类（渐进扩充） |
| 3 | FEATURES 子类管理 | profile 声明式 | 模块名在 profile JSON 中配置，不改代码 |
| 4 | 诊断维度存放位置 | ~~A 方案~~ → 被 #15 超越 | 初期决策为 workflow 层验证；v2.1 决定完全移除 workflows/ 后，诊断维度以 YAML `on_fail.collectors` 形式存在于 cases/ 声明中，无需独立 Python 模块 |
| 5 | 触发机制 | 两阶段 | 预筛（状态规则 + 轻量快照）→ 定向深度采集 |
| 6 | EvidenceBundle 格式 | 结构化 JSON | 机器可读，AI 友好 |
| 7 | 现有规则处理 | ~~删除根因规则~~ → 被 #16 超越 | 初期决策为删除根因规则保留可达性；v2.1 决定规则引擎整体替换为断言引擎，所有 v1 规则删除 |
| 8 | 预筛机制 | 状态规则 + 轻量快照匹配 | 状态规则判可达性，快照匹配决定采集哪些维度 |
| 9 | 测试处理 | 删除根因规则测试 | 新增断言引擎测试 + 用例执行测试 |
| 10 | AI 用例生成审核 | 先执行后审核 | 模块级用例先生成执行，全 pass 后审核入库；覆盖不全时补充用例 |
| 11 | AI 改码护栏 | 全自动 + N=5 回退 | safe 改动自动；最多循环 5 次，超限回退人工 |
| 12 | LE 驱动形态 | opencode 作为 driver | LE 是工具集，不内嵌 LLM；opencode 调用 LE 工具 |
| 13 | spec 范围 | 完整架构 + 分步实现 | 设计覆盖全部，实现分三步 |
| 14 | AI 生成模板 | templates/case-template.md | 约束 AI 生成用例的格式/断言选择/coverage/命名 |
| 15 | workflows/ 目录 | **完全移除** | Runner 上提 core，业务 100% 在 YAML；排查删除所有老方案遗留 |
| 16 | LoopAttempt 数据模型 | **彻底删除** | 删除 LoopAttempt/RuleMatch/ActionRecord；v2 只用 EvidenceBundle/TestCaseResult/CollectorResult |
| 17 | README 重新生成 | **强制** | 架构改动大，所有 README.md 实施时重新生成 |

---

## 5. 架构设计

### 5.1 三层职责分离

```
┌─────────────────────────────────────────────────────────────┐
│  opencode (AI Driver)                                        │
│                                                              │
│  1. 读代码/spec + templates/case-template.md → 生成用例草稿   │
│  2. 触发执行（le run）                                        │
│  3. 读 EvidenceBundle → 分析根因                              │
│  4. 修改 workspace 代码                                       │
│  5. 编译部署（le deploy binary/image）                        │
│  6. goto 2，直到全 pass 或 N=5 回退人工                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ EvidenceBundle JSON（契约）
┌──────────────────────▼──────────────────────────────────────┐
│  LE 框架 (loop_core 重构)                                    │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ case_loader  │  │assertion_    │  │ executor           │  │
│  │ (YAML加载)   │→ │ engine       │→ │ (执行用例+采集)     │  │
│  │              │  │ (断言求值)    │  │                    │  │
│  └─────────────┘  └──────────────┘  └────────┬───────────┘  │
│                                                │              │
│  ┌─────────────┐                               │              │
│  │ runner       │←──────────────────────────────┘              │
│  │(通用LoopRunner│  ┌──────────────────────────────────────┐   │
│  │ 场景无关)     │  │ evidence.py (EvidenceBundle JSON 输出)│   │
│  └─────────────┘  └──────────────────────────────────────┘   │
│                                                              │
│  保留：transport.py / config.py / observer.py / cycles.py    │
└─────────────────────────────────────────────────────────────┘
                       │ transport 抽象
┌──────────────────────▼──────────────────────────────────────┐
│  connection 层 (provider)                                    │
│  Rp5SerialTransport / AutomationClient / Windows Host        │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 目录结构（v2.1 最终形态）

```
engineering/loop/
├── README.md                          # 重新生成
├── WORKFLOW.md                        # 重写
├── bin/
│   └── le.sh                          # 新增：统一 CLI 入口（替代 loop_boot_failure_debug.sh）
├── core/python/loop_core/             # 重构
│   ├── models.py                      # 重写（删 LoopAttempt/RuleMatch/ActionRecord，加 v2 模型）
│   ├── assertion_engine.py            # 新增
│   ├── case_loader.py                 # 新增
│   ├── evidence.py                    # 新增
│   ├── executor.py                    # 新增
│   ├── collector.py                   # 新增
│   ├── runner.py                      # 新增（通用 LoopRunner）
│   ├── cli.py                         # 新增（le run/gen-cases/deploy）
│   ├── report.py                      # 改造（渲染 EvidenceBundle）
│   ├── config.py                      # 保留
│   ├── transport.py                   # 保留
│   ├── observer.py                    # 保留
│   ├── cycles.py                      # 保留（降级为工具）
│   ├── rules.py                       # ❌ 删除
│   └── actions.py                     # ❌ 删除
├── cases/                             # 新增：声明式用例
│   ├── common/
│   │   └── shell.yaml                 # shell_reachable / prompt_visible
│   ├── modules/                       # 模块级用例（AI 生成，第二步）
│   └── system/
│       └── boot-success.yaml          # 系统级 boot 验收
├── templates/                         # 新增：AI 生成约束
│   └── case-template.md               # 用例生成模板
├── connection/                        # 保留不动
│   ├── profiles/devices/rp5/
│   └── providers/rp5-serial/
├── scripts/                           # 保留不动
│   └── start_rp5_serial_host.bat
└── ❌ workflows/                      # 整个目录移除（boot-failure-debug-loop/）
```

### 5.3 用例层（声明式 YAML）

用例是整个架构的核心输入。采用 YAML 声明式格式，框架只执行不解析逻辑。

#### 5.3.1 用例文件格式

```yaml
# common/shell.yaml — 公共原子用例示例
suite: shell
version: 1

cases:
  - id: shell_reachable
    description: "shell prompt 可见，设备可达"
    command: ""                  # 空命令探测 prompt
    assert:
      type: prompt_visible
    severity: critical           # critical=fail 阻断; warn=仅记录
    on_fail:
      collectors: []
    tags: [boot, shell]

  - id: prompt_visible
    description: "发送回车后 prompt 出现"
    command: ""
    assert:
      type: prompt_visible
    severity: critical
    on_fail:
      collectors: []
    tags: [boot, shell]
```

```yaml
# system/boot-success.yaml — 系统级用例（include 组合）
suite: boot-success
version: 1
include:
  - common/shell

cases:
  - id: boot_completed
    description: "sys.boot_completed 属性为 1"
    command: "getprop sys.boot_completed"
    assert:
      type: contains
      value: "1"
    severity: critical
    requires: [shell_reachable]  # 前置依赖：shell_reachable fail 时本用例 skip
    on_fail:
      collectors: [boot_log, init_log]
    tags: [boot, system]

  - id: zygote_running
    description: "zygote 服务处于 running 状态"
    command: "getprop init.svc.zygote"
    assert:
      type: contains
      value: "running"
    severity: critical
    requires: [shell_reachable]
    on_fail:
      collectors: [crash_dump, init_log]
    tags: [boot, android_core]

collectors:                      # suite 级 collector 定义
  crash_dump:
    commands:
      - "logcat -b crash -d"
      - "ls -la /data/tombstones/"
    hints: "关注 abort message / signal / fault addr"
  init_log:
    commands:
      - "getprop init.svc.*"
      - "logcat -b system -d"
    hints: "关注 service 重启频率 / 退出信号"
  boot_log:
    commands:
      - "dmesg"
    hints: "关注 boot 时序 / init 阶段卡点"
```

#### 5.3.2 断言类型

| type | 说明 | 参数 | 示例 |
|------|------|------|------|
| `contains` | 输出包含指定文本 | `value: str` | `contains: "running"` |
| `regex` | 输出匹配正则 | `pattern: str` | `pattern: "inet \\d+\\.\\d+\\.\\d+\\.\\d+"` |
| `equals` | 输出完全等于 | `value: str` | `equals: "1"` |
| `prompt_visible` | prompt 标记可见 | （无参数，由 transport 判断） | 检测 shell prompt |
| `not_contains` | 输出不包含 | `value: str` | `not_contains: "error"` |
| `exit_code_zero` | 命令退出码为 0 | （无参数） | 标准成功检查 |

未来可扩展：`timing`（时序断言）、`json_path`（JSON 输出断言）、`custom`（自定义脚本）。

#### 5.3.3 用例依赖与短路

```yaml
cases:
  - id: shell_reachable
    assert: { type: prompt_visible }
  - id: zygote_running
    requires: [shell_reachable]    # shell_reachable fail → 本用例 skip（非 fail）
    command: "getprop init.svc.zygote"
    assert: { type: contains, value: "running" }
  - id: adbd_running
    requires: [zygote_running]     # zygote fail → 本用例 skip
    command: "getprop init.svc.adbd"
    assert: { type: contains, value: "running" }
```

**依赖执行规则：**
- `requires` 中任一前置用例 `fail` → 当前用例标记 `skipped`
- `requires` 中前置用例 `skipped` → 当前用例也 `skipped`（传播）
- 无 `requires` 或前置全 `pass` → 正常执行
- case_loader 拓扑排序检测环，有环报错

#### 5.3.4 参数化用例（未来扩展，第一步不实现）

```yaml
cases:
  - id: service_running
    parameters:
      - name: service_name
        values: [surfaceflinger, audioserver, netd]
    command: "getprop init.svc.{{service_name}}"
    assert: { type: contains, value: "running" }
```

展开为 3 个独立用例（service_running[surfaceflinger] / [audioserver] / [netd]），各自独立 pass/fail/skip。

### 5.4 EvidenceBundle 格式（AI 契约）

LE 框架执行完用例后，输出 `evidence_bundle.json` 供 AI 分析。

```json
{
  "bundle_id": "eb-20260619-223606",
  "device_id": "rp5",
  "suite": "boot-success",
  "timestamp": "2026-06-19T22:36:06+08:00",
  "summary": {
    "total": 8,
    "passed": 6,
    "failed": 1,
    "skipped": 1,
    "overall": "FAIL"
  },
  "cases": [
    {
      "id": "shell_reachable",
      "status": "pass",
      "command": "",
      "output_preview": "console:/ $",
      "assertion": { "type": "prompt_visible" },
      "duration_sec": 0.3
    },
    {
      "id": "zygote_running",
      "status": "fail",
      "command": "getprop init.svc.zygote",
      "output": "stopped\n",
      "assertion": { "type": "contains", "value": "running" },
      "duration_sec": 1.2,
      "failure_reason": "expected 'running', got 'stopped'",
      "triggered_collectors": ["crash_dump", "init_log"]
    },
    {
      "id": "adbd_running",
      "status": "skipped",
      "reason": "dependency 'zygote_running' failed"
    }
  ],
  "evidence": {
    "crash_dump": {
      "commands": ["logcat -b crash -d", "ls -la /data/tombstones/"],
      "outputs": [
        { "command": "logcat -b crash -d", "lines": ["--------- beginning of crash", "..."] },
        { "command": "ls -la /data/tombstones/", "lines": ["total 16", "tombstone_00 ..."] }
      ],
      "hints": "关注 abort message / signal / fault addr"
    },
    "init_log": {
      "commands": ["getprop init.svc.*", "logcat -b system -d"],
      "outputs": [],
      "hints": "关注 service 重启频率 / 退出信号"
    }
  },
  "device_profile": {
    "device_id": "rp5",
    "transport": "serial"
  }
}
```

### 5.5 LE 执行流程

```
1. le run --suite boot-success --target rp5
   │
   ├── 2. case_loader 加载 YAML（解析 include/requires/parameters）
   │
   ├── 3. transport 连接设备（serial/adb/fixture）
   │
   ├── 4. LoopRunner.run() → CaseExecutor.execute_suite()
   │   ├── 拓扑序执行 cases（处理依赖）
   │   ├── 每条 case: transport.send_line + capture_window → AssertionEngine.evaluate
   │   └── fail 时记录，检查 on_fail.collectors
   │
   ├── 5. 执行触发的 collectors（深度证据采集）
   │   └── 每个 collector 执行其 commands 列表（同 suite 内去重）
   │
   ├── 6. 组装 EvidenceBundle JSON
   │
   └── 7. 输出到 artifacts_dir/evidence_bundle.json + summary.txt
       │
       ▼
    opencode 读取 → AI 分析 → 改码 → le deploy → le run（循环）
```

### 5.6 AI 自动修复闭环

```
                    ┌──────────────────────┐
                    │ le run (执行用例)     │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │ EvidenceBundle JSON   │
                    └──────────┬───────────┘
                               │
                ┌──────────────▼──────────────┐
                │ opencode 分析 fail 用例      │
                │ 1. 读证据 → 根因分析         │
                │ 2. 定位 workspace 源码      │
                │ 3. 生成修复 diff             │
                └──────────────┬──────────────┘
                               │
                ┌──────────────▼──────────────┐
                │ le deploy (编译+部署)        │
                │ ├── binary → 自动替换        │
                │ └── image → 用户确认刷写     │
                └──────────────┬──────────────┘
                               │
                ┌──────────────▼──────────────┐
                │ 全量用例回归                  │
                │ (检测 AI 引入的回归)          │
                └──────────────┬──────────────┘
                               │
                    ┌──────────▼───────────┐
                    │ 全 pass?              │
                    ├── yes → 修复成功，退出 │
                    └── no → 循环计数 +1     │
                               │
                    ┌──────────▼───────────┐
                    │ 计数 >= 5?            │
                    ├── yes → 回退人工       │
                    └── no → goto le run    │
```

**循环控制规则：**
1. **最大循环 5 次**：超限自动升级人工，附完整 EvidenceBundle 历史
2. **回归保护**：每次改码后跑全量用例，若引入新 fail（之前 pass 的用例变 fail），标记为"AI 回归"，阻断当前修复方向
3. **改动关联**：每次修复必须关联到具体 fail 用例；用例仍 fail 则回滚改动（`git checkout`），不计入循环次数

---

## 6. templates/case-template.md（AI 生成用例约束模板）

### 6.1 目的

约束 AI（opencode）从代码 + spec 生成用例时的格式、质量、coverage。

### 6.2 内容大纲

```markdown
# Loop Engineering 用例生成模板

## 1. 用例文件格式规范（YAML schema）
   - suite/version/cases/collectors 字段定义
   - 必填 vs 可选字段（id/description/command/assert/severity 必填）

## 2. 断言类型选择矩阵
   | 场景 | 推荐断言 | 示例 |
   |------|---------|------|
   | 进程状态 | contains | "running" |
   | IP 地址 | regex | "inet \d+\.\d+\.\d+\.\d+" |
   | 布尔属性 | equals | "1" |
   | prompt 可见 | prompt_visible | - |
   | 排除错误 | not_contains | "error" |
   | 命令成功 | exit_code_zero | - |

## 3. coverage 要求
   - 每个公开 HAL 接口至少 1 条用例
   - 每个 init service 至少 1 条用例
   - 每个设备节点至少 1 条存在性检查
   - 关键属性（sys.boot_completed 等）必须覆盖
   - 用例标注来源（code/spec）

## 4. 命名规范
   - suite 名与目录一致（snake_case）
   - case id 全局唯一（snake_case）
   - collector 名语义化（crash_dump/init_log/network_log）

## 5. collector 选择指南
   | fail 类型 | 推荐 collector |
   |-----------|--------------|
   | 进程崩溃 | crash_dump（logcat -b crash + tombstones）|
   | 服务未启动 | init_log（getprop init.svc.* + logcat system）|
   | 网络问题 | network_log（logcat system + ip addr）|
   | boot 卡死 | boot_log（dmesg + boottime）|

## 6. 好用例 vs 坏用例
   ✅ 确定性、可重复、语义清晰、单一职责
   ❌ 模糊匹配、依赖时序、隐式状态、一条用例检查多件事

## 7. 生成 checklist
   - [ ] 每条用例有 description
   - [ ] severity 明确（critical/warn）
   - [ ] 依赖声明完整（requires）
   - [ ] on_fail 指定合理 collector
   - [ ] 命名符合 snake_case
   - [ ] suite/version 字段存在
```

---

## 7. core 模块重构映射

### 7.1 模块去留表

| core 模块 | 命运 | 重构内容 |
|-----------|------|---------|
| `models.py` | **重写** | 删除 LoopAttempt/RuleMatch/ActionRecord；保留 ObservedLine；新增 TestCaseResult/EvidenceBundle/CollectorResult |
| `transport.py` | **保留** | 不变（BaseTransport + FixtureTransport） |
| `observer.py` | **保留** | capture_snapshot 仍用于 prompt 探测 |
| `config.py` | **保留** | DeviceProfile/BaseWorkflowConfig/merge_profiles 不变 |
| `report.py` | **改造** | 渲染 EvidenceBundle（删 LoopAttempt 渲染，加用例结果矩阵） |
| `cycles.py` | **保留** | 降级为可选分析工具，不进用例执行主路径 |
| `rules.py` | **❌ 删除** | → `assertion_engine.py` |
| `actions.py` | **❌ 删除** | → `executor.py` + `collector.py` |
| **新增** | `assertion_engine.py` | 断言引擎（contains/regex/equals/prompt_visible/not_contains/exit_code_zero） |
| **新增** | `case_loader.py` | YAML 加载 + include/requires/parameters 解析 + 环检测 |
| **新增** | `evidence.py` | EvidenceBundle JSON 组装与输出 |
| **新增** | `executor.py` | CaseExecutor（用例执行 + collector 触发） |
| **新增** | `collector.py` | 深度证据采集执行器 |
| **新增** | `runner.py` | **通用 LoopRunner**（吸收 workflow runner 通用逻辑，场景无关） |
| **新增** | `cli.py` | **统一 CLI 入口**（le run/gen-cases/deploy） |
| **新增（第三步）** | `loop_ctrl.py` | 循环控制（最大次数/回归检测/升级人工） |
| **新增（第三步）** | `deployer.py` | binary 自动替换 / 镜像确认 |

### 7.2 数据模型（models.py 重写）

```python
# 保留
@dataclass
class ObservedLine:
    t: float
    text: str
    cycle_id: int = 0

# ❌ 删除：RuleMatch / ActionRecord / LoopAttempt

# ✅ 新增
@dataclass
class TestCaseResult:
    """单个用例的执行结果。"""
    id: str
    suite: str
    status: str                      # "pass" | "fail" | "skipped" | "error"
    command: str = ""
    output: str = ""                 # 完整输出
    output_preview: str = ""         # 摘要（前 N 行）
    assertion: dict = field(default_factory=dict)
    duration_sec: float = 0.0
    failure_reason: str = ""
    skip_reason: str = ""
    triggered_collectors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

@dataclass
class CollectorResult:
    """collector 执行结果。"""
    name: str
    commands: list[str]
    outputs: list[dict]              # [{command, lines, duration_sec}]
    hints: str = ""

@dataclass
class EvidenceBundle:
    """LE 框架输出给 AI 的证据包。"""
    bundle_id: str
    device_id: str
    suite: str
    timestamp: str
    summary: dict                    # {total, passed, failed, skipped, overall}
    cases: list[TestCaseResult]
    evidence: dict[str, CollectorResult]
    device_profile: dict = field(default_factory=dict)
```

### 7.3 断言引擎（assertion_engine.py）

```python
class AssertionEngine:
    """断言求值引擎。"""

    def evaluate(self, assertion: dict, context: AssertionContext) -> AssertionResult:
        """求值单条断言。

        Args:
            assertion: YAML 中的 assert 字段 {type, value/pattern}
            context: 包含 output / prompt_visible / exit_code

        Returns:
            AssertionResult(passed=bool, reason=str)
        """
```

```python
@dataclass
class AssertionContext:
    """断言求值上下文。"""
    output: str
    prompt_visible: bool = False
    exit_code: int | None = None

@dataclass
class AssertionResult:
    """断言求值结果。"""
    passed: bool
    reason: str = ""
```

### 7.4 用例加载器（case_loader.py）

```python
@dataclass
class TestCase:
    """加载后的用例定义。"""
    id: str
    suite: str
    command: str
    assert_spec: dict
    severity: str                    # "critical" | "warn"
    requires: list[str]
    on_fail: dict                    # {collectors: [...]}
    tags: list[str]
    description: str = ""

@dataclass
class CaseSuite:
    """用例集。"""
    name: str
    version: int
    cases: list[TestCase]
    collectors: dict[str, dict]      # name → {commands, hints}

def load_suite(suite_path: str, case_dirs: list[str]) -> CaseSuite:
    """加载 YAML 用例集，解析 include/requires。

    - include: 合并被引用 suite 的 cases 和 collectors
    - requires: 拓扑排序 + 环检测
    """
```

### 7.5 通用 LoopRunner（runner.py）

```python
class LoopRunner:
    """通用 LE 执行器。场景无关，纯用例驱动。

    所有场景（boot-success/lcview/lciod）共用此 runner。
    新场景只需写 YAML 用例，零 Python 代码。
    """

    def __init__(self, device_profile: DeviceProfile, transport: BaseTransport,
                 suite: CaseSuite):
        self.device_profile = device_profile
        self.transport = transport
        self.suite = suite
        self.executor = CaseExecutor(transport, AssertionEngine())

    def run(self) -> EvidenceBundle:
        """执行用例集并返回证据包。"""
        if not self.transport.acquire_writer():
            return self._build_failure_bundle("writer_busy")
        try:
            return self.executor.execute_suite(
                self.suite,
                device_id=self.device_profile.device_id,
            )
        finally:
            self.transport.release()

    def _build_failure_bundle(self, reason: str) -> EvidenceBundle:
        """构建 writer 获取失败时的 EvidenceBundle。"""
```

### 7.6 CaseExecutor（executor.py）

```python
class CaseExecutor:
    """用例执行器。

    职责：执行用例 → 求值断言 → 触发 collector → 组装 EvidenceBundle
    """

    def __init__(self, transport, assertion_engine: AssertionEngine):
        self.transport = transport
        self.engine = assertion_engine

    def execute_suite(self, suite: CaseSuite, device_id: str,
                      capture_timeout: float = 5.0,
                      recent_limit: int = 400) -> EvidenceBundle:
        """执行完整用例集。

        执行顺序：拓扑序（处理 requires 依赖）
        fail 用例触发 on_fail.collectors（同 suite 内去重）
        skipped 用例不触发 collector
        """

    def _execute_case(self, case: TestCase, results: dict[str, TestCaseResult],
                      capture_timeout: float) -> TestCaseResult:
        """执行单个用例（含依赖检查）。"""

    def _run_collector(self, name: str, spec: dict,
                      capture_timeout: float) -> CollectorResult:
        """执行深度证据采集。"""
```

---

## 8. CLI 工具集

### 8.1 `le run` — 执行用例集

```bash
# live 模式
le.sh run \
  --suite boot-success \
  --target rp5 \
  --host 127.0.0.1 --port 9700 \
  --device-profile <path> \
  --case-dirs <common_dir>,<system_dir> \
  --artifacts-dir <dir>

# fixture 模式（离线回放）
le.sh run \
  --suite boot-success \
  --fixture <jsonl> \
  --case-dirs <dir> \
  --artifacts-dir <dir>
```

输出：`<artifacts_dir>/evidence_bundle.json` + `<artifacts_dir>/summary.txt`

### 8.2 `le gen-cases` — AI 辅助用例生成（第二步实现）

```bash
le.sh gen-cases \
  --module lcview \
  --source ~/workspace/path/to/lcview \
  --spec docs/specs/lcview-design.md \
  --template templates/case-template.md \
  --output cases/modules/lcview.yaml
```

行为：扫描源码中的可测试点（HAL 接口、属性、设备节点），生成用例骨架 YAML。AI（opencode）在此基础上完善断言。

### 8.3 `le deploy` — 部署（第二步实现）

```bash
# binary 自动替换
le.sh deploy binary \
  --module lcview \
  --binary ~/workspace/out/target/.../lcview.so \
  --target rp5 --host 127.0.0.1 --port 9700

# 镜像部署（需用户确认）
le.sh deploy image \
  --image ~/workspace/out/.../boot.img \
  --target rp5
```

### 8.4 入口脚本

```bash
# engineering/loop/bin/le.sh — 统一 LE CLI 入口
#!/bin/bash
# 调用 python3 -m loop_core.cli "$@"
```

---

## 9. workflows/ 完全移除清单

### 9.1 删除文件

| 文件 | 理由 |
|------|------|
| `workflows/boot-failure-debug-loop/bin/loop_boot_failure_debug.sh` | 替换为 `bin/le.sh` |
| `workflows/boot-failure-debug-loop/python/boot_failure_debug/__init__.py` | workflows/ 移除 |
| `workflows/boot-failure-debug-loop/python/boot_failure_debug/config.py` | 通用 LoopRunner 不需要 per-scenario config |
| `workflows/boot-failure-debug-loop/python/boot_failure_debug/rules.py` | 规则引擎移除 |
| `workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py` | CaseExecutor 替代 |
| `workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py` | 通用 LoopRunner 替代 |
| `workflows/boot-failure-debug-loop/python/boot_failure_debug/cli.py` | 统一 cli.py 替代 |
| `workflows/boot-failure-debug-loop/python/tests/` 全部测试文件 | 随 workflows/ 移除 |

### 9.2 迁移文件

| 源 | 目标 | 内容 |
|----|------|------|
| `workflows/.../tests/fixtures/*.jsonl`（5 个） | `core/python/tests/fixtures/` | fixture 模式复用（v2 FixtureTransport 兼容） |

### 9.3 修改文件

| 文件 | 改动 |
|------|------|
| `profiles/boot-failure-debug/default.json` | 简化：删除 v1 阈值（l1_commands/l2_actions/reboot_loop_threshold/quiet_window_sec 等）；配置移入 suite YAML |
| `connection/profiles/devices/rp5/default.json` | 简化：删除 panic_markers/boot_markers/hang_markers（迁入用例 collectors）；保留 device_id/transport/prompt_markers/reboot_markers/line_ending |
| `engineering/loop/README.md` | **重新生成**：v2 架构说明 + 联合回归命令（去掉 workflows 路径）+ 目录结构 |
| `engineering/loop/WORKFLOW.md` | **重写**：v2 流程 + core 模块清单 + 扩展指南（"写 YAML 即可"）+ 新遗留点 |
| `engineering/loop/connection/providers/rp5-serial/README.md` | 检查是否引用 workflows（若有则更新） |
| `engineering/loop/core/python/loop_core/__init__.py` | 更新模块 docstring |

### 9.4 README 重新生成清单（强制）

实施修改时以下 README.md 必须重新生成：

| README | 重新生成理由 |
|--------|------------|
| `engineering/loop/README.md` | 架构全变（目录结构/命令/回归方法） |
| `engineering/loop/WORKFLOW.md` | 流程全变（用例驱动替代规则匹配） |
| `engineering/loop/core/README.md`（如有） | 模块清单变更 |
| `engineering/loop/connection/providers/rp5-serial/README.md` | 检查是否引用 workflows，若有则更新 |
| 其他引用 loop engineering 的 README | 排查后按需更新 |

---

## 10. 测试重构

### 10.1 core 测试

| 文件 | 操作 | 说明 |
|------|------|------|
| `test_rules.py`（7 tests） | **❌ 删除** | rules.py 删除 |
| `test_actions.py`（4 tests） | **❌ 删除** | actions.py 删除 |
| `test_models.py`（9 tests） | **重写** | 删 LoopAttempt/RuleMatch/ActionRecord 测试；加 TestCaseResult/EvidenceBundle 测试 |
| `test_report.py`（15 tests） | **重写** | 渲染 EvidenceBundle |
| `test_transport.py`（13 tests） | **保留** | transport 不变 |
| `test_observer.py`（12 tests） | **保留** | observer 不变 |
| `test_cycles.py`（9 tests） | **保留** | cycles 不变 |
| `test_config.py`（7 tests） | **保留** | config 不变 |
| `test_assertion_engine.py` | **新增** | 6 种断言类型测试 |
| `test_case_loader.py` | **新增** | YAML 加载/include/requires/环检测 |
| `test_evidence.py` | **新增** | EvidenceBundle 组装 |
| `test_executor.py` | **新增** | CaseExecutor + collector 去重 |
| `test_runner.py` | **新增** | 通用 LoopRunner |

### 10.2 workflow 测试（全部删除）

`workflows/boot-failure-debug-loop/python/tests/` 下所有文件随 workflows/ 移除。

---

## 11. 实现路线（三步走）

### 第一步：核心闭环验证（本次实现）

**目标：** 用 boot-success 场景验证"用例驱动 + EvidenceBundle"全流程。

**范围：**
- core 新增：`assertion_engine.py` / `case_loader.py` / `evidence.py` / `executor.py` / `collector.py` / `runner.py` / `cli.py`
- core 重写：`models.py`（删除 v1 模型，加 v2 模型）
- core 改造：`report.py`
- core 删除：`rules.py` / `actions.py`
- workflows/：**整个目录删除**（fixtures 迁移到 core/tests/）
- 用例新增：`cases/common/shell.yaml` + `cases/system/boot-success.yaml`
- 模板新增：`templates/case-template.md`
- 入口新增：`bin/le.sh`
- profile 简化：device profile + workflow profile 删除 v1 字段
- 文档：**README.md / WORKFLOW.md 重新生成**
- 测试：全量重写（core 侧）

**验收标准：**
1. 联合回归全绿（新测试）
2. `le.sh run --suite boot-success --fixture <jsonl>` 能在 fixture 模式下跑通
3. EvidenceBundle JSON 格式正确
4. opencode 能读 EvidenceBundle 并给出分析（手动验证）
5. README.md / WORKFLOW.md 反映 v2 架构

### 第二步：模块级 LE 验证

**目标：** 选 lcview 或 lciod，验证 AI 生成用例 → 执行 → 修复闭环。

**范围：**
- `le gen-cases` 实现
- `le deploy binary` 实现
- AI 生成 lcview 用例（遵循 template）→ 执行 → fail → AI 分析修复 → 重测
- 循环控制（N=5）基础实现

### 第三步：补全护栏与镜像部署

**目标：** 生产级可用。

**范围：**
- `le deploy image`（用户确认刷写）
- `loop_ctrl.py`（回归检测 / 升级人工）
- 用例版本管理
- 系统级 boot-stability 场景

---

## 12. 约束与风险

### 12.1 技术约束

1. **YAML 解析**：需引入 `pyyaml` 依赖（当前项目零三方依赖）。评估：pyyaml 是 Python 生态标准库级别，可接受。
2. **transport 接口不变**：CaseExecutor 依赖 BaseTransport 的 `send_line` / `capture_window` / `wait_for_pattern`，不新增抽象方法。
3. **fixture 模式兼容**：FixtureTransport 需支持用例执行（send_line 后 capture_window 返回模拟输出）。
4. **README 强制重新生成**：架构改动大，实施时所有 README.md 必须重新生成。

### 12.2 架构风险

| 风险 | 缓解 |
|------|------|
| 断言类型不够表达复杂场景 | 初期 6 种够用；用例格式预留 `custom` 类型扩展点 |
| 用例间依赖形成环 | case_loader 拓扑排序检测环，有环报错 |
| AI 生成的用例自证陷阱 | 用例标注来源（code/spec）；关键用例人工审核 |
| AI 改码振荡循环 | N=5 回退 + 回归检测 + 改动回滚 |
| EvidenceBundle 过大 | output 截断 + output_preview；大输出单独落文件引用 |

### 12.3 迁移风险

| 风险 | 缓解 |
|------|------|
| 删除 rules.py/actions.py 破坏现有测试 | 测试同步重写，联合回归验证 |
| workflows/ 整体移除影响面大 | 完整排查清单（第 9 节），逐项确认 |
| LoopAttempt 删除影响 report.py | report.py 同步重写为 EvidenceBundle 渲染 |

---

## 13. 开放问题（待实现阶段决策）

1. **参数化用例的执行粒度**：`service_running` 参数化 3 个 service，展开为 3 个独立用例（独立 pass/fail/skip）。第一步不实现参数化。
2. **collector 去重**：同 suite 内同 collector 只执行一次。
3. **prompt_visible 断言的 transport 交互**：需要 transport 支持"不发命令、仅检测 prompt"的能力。当前 `wait_for_pattern` 可复用。
4. **用例超时**：每条用例的命令执行超时。suite 级默认 + case 级覆盖（YAML `timeout` 字段，第一步用 suite 级默认值）。

---

## 14. 附录

### 14.1 现有架构数据流（v1，将被替换）

```
fixture/live → transport → observer.capture_snapshot → rules.evaluate_rules
→ rules.classify → actions.plan_actions → runner._execute_planned_actions
→ report.write_report_bundle → report.json + summary.txt
```

### 14.2 新架构数据流（v2）

```
case_loader.load_suite → LoopRunner.run → CaseExecutor.execute_suite
  ├── per case: transport.send_line + capture_window → AssertionEngine.evaluate
  ├── per fail: Collector.run → evidence
  └── → EvidenceBundle JSON → opencode 分析
```

### 14.3 模块依赖图（v2）

```
models (纯数据: ObservedLine/TestCaseResult/CollectorResult/EvidenceBundle)
  ▲
  ├── assertion_engine    (AssertionContext/AssertionResult)
  ├── case_loader         (TestCase, CaseSuite)
  ├── evidence            (EvidenceBundle 组装)
  ├── executor            (CaseExecutor) ──► assertion_engine + case_loader + evidence + collector
  ├── collector           (深度证据采集)
  ├── runner              (LoopRunner) ──► executor
  ├── cli                 (le run/gen-cases/deploy) ──► runner + case_loader
  ├── observer            (保留, capture_snapshot)
  ├── cycles              (降级为工具)
  ├── config              (保留)
  ├── transport           (保留)
  └── report              (改造, 渲染 EvidenceBundle)

[删除] rules.py, actions.py, workflows/
[新增] assertion_engine, case_loader, evidence, executor, collector, runner, cli
[未来] loop_ctrl, deployer
```

### 14.4 文件清单（第一步实现范围）

**core 新增：**
```
engineering/loop/core/python/loop_core/
├── assertion_engine.py    # 新增
├── case_loader.py         # 新增
├── evidence.py            # 新增
├── executor.py            # 新增
├── collector.py           # 新增
├── runner.py              # 新增（通用 LoopRunner）
├── cli.py                 # 新增（统一 CLI 入口）
├── models.py              # 重写
├── report.py              # 改造
├── config.py              # 保留
├── transport.py           # 保留
├── observer.py            # 保留
├── cycles.py              # 保留
├── rules.py               # ❌ 删除
└── actions.py             # ❌ 删除
```

**core 测试：**
```
engineering/loop/core/python/tests/
├── test_assertion_engine.py  # 新增
├── test_case_loader.py       # 新增
├── test_evidence.py          # 新增
├── test_executor.py          # 新增
├── test_runner.py            # 新增
├── test_models.py            # 重写
├── test_report.py            # 重写
├── test_transport.py         # 保留
├── test_observer.py          # 保留
├── test_cycles.py            # 保留
├── test_config.py            # 保留
├── test_rules.py             # ❌ 删除
├── test_actions.py           # ❌ 删除
└── fixtures/*.jsonl          # 从 workflows/ 迁入
```

**workflows/ 删除：**
```
engineering/loop/workflows/   # ❌ 整个目录移除
```

**用例/模板/入口新增：**
```
engineering/loop/
├── bin/le.sh                           # 新增
├── cases/common/shell.yaml             # 新增
├── cases/system/boot-success.yaml      # 新增
└── templates/case-template.md          # 新增
```

**文档重新生成：**
```
engineering/loop/README.md              # 重新生成
engineering/loop/WORKFLOW.md            # 重新生成
```

**profile 简化：**
```
engineering/loop/profiles/boot-failure-debug/default.json          # 简化
engineering/loop/connection/profiles/devices/rp5/default.json     # 简化
```

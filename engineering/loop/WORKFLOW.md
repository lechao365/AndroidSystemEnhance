---
name: loop-engineering
description: loop engineering v2 工作流（用例驱动 + AI 修复闭环）
---

# Loop Engineering v2 Workflow

## 目标

AI 接管设备验收：执行用例 → 输出证据 → AI 分析 → 修复代码 → 重测 → 循环直到全 pass。

## 核心流程

```
1. AI 读代码/spec + template → 生成 YAML 用例
2. le run 执行用例 → EvidenceBundle JSON
3. 全 pass → 功能 OK
4. 有 fail → AI 读 EvidenceBundle 分析根因
5. AI 修改 workspace 代码
6. 编译部署（binary 自动 / 镜像确认）
7. goto 2，直到全 pass 或 N=5 回退人工
```

## 分层职责

| 层 | 职责 |
|----|------|
| opencode (AI) | 生成用例 / 分析证据 / 修复代码 |
| loop_core | 用例加载 / 断言求值 / 执行 / 证据输出 |
| cases/*.yaml | 场景定义（声明式，零 Python） |
| connection | 传输层（串口/ADB） |

## 规则复用模型

### FQN 命名

- case FQN = `<suite>.<id>`（如 `system.boot.zygote_running`）。
- collector FQN = `<suite>.<name>`（如 `common.shell.crash_dump`）。
- `requires` / `on_fail.collectors` 可写短名：loader 按本地命名空间 → 显式 FQN →
  全局唯一短名 三段式解析（见 `case_loader._resolve_case_links`）。

### 公共 suite 与诊断 collector 库

`cases/common/shell.yaml`（`common.shell`）提供：
- 原子用例 `shell_reachable`（作为系统用例的 `requires` 前置）。
- 公共诊断 collector：`boot_log` / `init_log` / `crash_dump` / `serial_recent`。
- `serial_recent` 为串口上下文 collector（`mode: serial_context`），无需 shell 可达
  即可获取 host transcript 路径、最近串口片段与重启周期，是 zygote 反复重启等场景的
  串口第一现场证据入口。

业务 suite 通过 `include: [common/shell]` 自动注入上述用例和 collector，
失败时直接用短名引用即可，无需重复定义。新场景应优先复用公共 collector，
仅在场景专属诊断（如 HAL / sensor 特定日志）时定义本地 collector。

### include 解析

- `include` 路径由 `--case-dirs` 解析；loader 在每个 case_dir 下找 `<name>.yaml`。
- 因此 `include: [common/shell]` 要求 `--case-dirs` 包含 `cases/` 根目录。

## core 模块清单

| 模块 | 职责 |
|------|------|
| `models.py` | ObservedLine / TestCaseResult / CollectorResult / EvidenceBundle |
| `assertion_engine.py` | 确定性断言（contains/regex/equals/prompt_visible/not_contains/exit_code_zero） |
| `case_loader.py` | YAML 加载 + include + requires 拓扑排序 |
| `executor.py` | 用例执行 + collector 触发（去重） |
| `collector.py` | 深度证据采集（含 `serial_context` 模式，消费 transport runtime context） |
| `runner.py` | 通用 LoopRunner（场景无关） |
| `evidence.py` | EvidenceBundle JSON 输出 |
| `report.py` | evidence.py 薄封装 |
| `cli.py` | 统一 CLI（le run / gen-cases / deploy） |
| `config.py` | DeviceProfile（设备语义 + 默认执行参数） |
| `transport.py` | BaseTransport + FixtureTransport |
| `observer.py` | capture_snapshot（prompt 探测） |
| `cycles.py` | cycle 切分工具（可选） |

## 扩展新场景

1. 参照 `templates/case-template.md`
2. 在 `cases/system/` 下创建 `<scenario>.yaml`
3. `le.sh run --suite <path> ...`

无需写任何 Python 代码。

## 断言类型

| type | 用途 |
|------|------|
| `contains` | 输出包含文本 |
| `regex` | 输出匹配正则 |
| `equals` | 输出完全等于 |
| `prompt_visible` | shell prompt 可见 |
| `not_contains` | 输出不包含文本 |
| `exit_code_zero` | 退出码为 0 |

## EvidenceBundle 串口上下文

EvidenceBundle `serial_context` 字段提供串口第一现场证据：
- `transcript_path`：host 持续落盘的串口 transcript 文件
- `serial_snippet`：最近一段串口片段
- `reboot_cycles`：基于 reboot marker 估算的最近重启周期数

shell 不可达时，AI/人工应优先分析 `serial_context`；shell 可达时再结合 `init_log` / `crash_dump` 等 collector 证据。

## 遗留点

1. **gen-cases 未实现**：第二步实现 AI 用例生成（reboot 诊断闭环属范围 A，已实现）
2. **deploy 未实现**：第二步实现 binary/image 部署（范围 A 不含 deploy）
3. **loop_ctrl 未实现**：第三步实现循环控制（N=5 / 回归检测 / 升级人工）
4. **参数化用例**：case_loader 预留 parameters 字段，第一步未实现展开

## AI 诊断报告约束（范围 A reboot 诊断闭环）

当 AI（opencode）通过 `/le` 触发 reboot 诊断闭环并收到 EvidenceBundle 后，**必须**按
`engineering/harness/templates/diagnosis-report-template.md` 产出诊断报告：

1. 报告路径：`engineering/output/runs/<run-id>/diagnosis-report.md`（与 EvidenceBundle 同目录）
2. 报告含 6 节：结论 / 证据链 / 根因分析 / 修复建议 / 建议新增 case / 循环终止建议
3. 修复建议必须具体到 workspace 文件路径和函数名
4. YAML 建议（第 5 节）不自动应用，只给人 review（G2 决策）
5. AI 不自动修改 boot-success.yaml

reboot 诊断闭环的数据流：
- `/le run --suite boot-success.yaml --host <ip> --port 9700 ...`
- executor 遇到 `action: reboot` case → 调 transport.reboot_and_wait
- reboot_and_wait 三级渐进判定（L1 boot 开始 / L2 init 阶段 / L3 boot_completed 验证）
- 后续 case（requires: [trigger_reboot]）在设备回来后正常执行
- on_fail 触发 collectors（含新增 kmsg）
- EvidenceBundle 落盘 → AI 读后按模板产出诊断报告

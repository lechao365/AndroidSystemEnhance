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

## core 模块清单

| 模块 | 职责 |
|------|------|
| `models.py` | ObservedLine / TestCaseResult / CollectorResult / EvidenceBundle |
| `assertion_engine.py` | 确定性断言（contains/regex/equals/prompt_visible/not_contains/exit_code_zero） |
| `case_loader.py` | YAML 加载 + include + requires 拓扑排序 |
| `executor.py` | 用例执行 + collector 触发（去重） |
| `collector.py` | 深度证据采集 |
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

## 遗留点

1. **gen-cases / deploy 未实现**：第二步实现 AI 用例生成和 binary/image 部署
2. **loop_ctrl 未实现**：第三步实现循环控制（N=5 / 回归检测 / 升级人工）
3. **参数化用例**：case_loader 预留 parameters 字段，第一步未实现展开

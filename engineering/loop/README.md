# Loop Engineering

`engineering/loop/` 只承载 loop engineering 本身，不重构 `engineering/harness/`。

## 当前范围

- `core/`：loop 通用框架层（数据模型、transport 抽象、观察器、规则引擎框架、报告渲染）
- `connection/`：连接域，定义协议、provider profile 与具体 provider 实现
- `workflows/`：业务闭环（如启动失败调试），消费 core + connection
- `profiles/`：设备级/场景级配置

## 已实现模块

- `core/python/loop_core/`：通用框架（9 个模块，76 个独立测试）
- `connection/providers/rp5-serial/`：Windows Host 独占物理串口 + WSL2 Client 三模式接入 + AutomationClient 双通道 + Rp5SerialTransport
- `workflows/boot-failure-debug-loop/` v1：启动失败诊断闭环（消费 loop_core）
- `profiles/`：device profile + workflow profile + override 合并

## 测试

联合回归（三套测试目录）：

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/workflows/boot-failure-debug-loop/python" \
  python3 -m pytest \
    engineering/loop/core/python/tests \
    engineering/loop/connection/providers/rp5-serial/python/tests \
    engineering/loop/workflows/boot-failure-debug-loop/python/tests \
    -q --import-mode=importlib
```

> 注意：由于三个测试目录都叫 `tests`，必须使用 `--import-mode=importlib` 避免包名冲突。

## 与 harness 的关系

loop 与 harness 保持解耦，仅在 bash 入口层复用 harness 的 observability 基础设施：

- `engineering/harness/lib/harness_bootstrap.sh`
- `engineering/harness/lib/harness_observability.sh`
- `engineering/harness/rules/script-observability.md`

loop **不依赖** harness 的业务 workflow 逻辑，也不把 patchs/workspace 的业务规则耦合进 loop 核心。

## 日志落点

loop 的 bash 入口脚本日志统一落到 harness 的日志目录，采用前缀命名以避免与现有 harness 脚本混淆：

- 落点：`engineering/harness/log/<script-name>/`
- script-name 前缀：
  - `loop-rp5-serial-monitor`
  - `loop-rp5-serial-interactive`
  - `loop-rp5-serial-automation`
  - `loop-boot-failure-debug`

Windows Host 本地轻量日志由 Host 自行维护，不强制套用 harness 完整维测框架，落点不强制在 `engineering/harness/log/`。

## 参考

- 总体设计：`docs/specs/2026-06-19-loop-engineering-design.md`
- core 抽取设计：`docs/specs/2026-06-19-loop-core-extraction-design.md`
- shell 基础链路修复：`docs/specs/2026-06-19-loop-shell-foundation-fix-design.md`
- 实施计划：`docs/plans/2026-06-19-loop-core-extraction.md`

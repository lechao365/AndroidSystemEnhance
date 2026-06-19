# Loop Engineering

`engineering/loop/` 只承载 loop engineering 本身，不重构 `engineering/harness/`。

## 当前范围

- `connection/`：连接域，定义协议、provider profile 与具体 provider 实现
- `workflows/`：业务闭环（如启动失败调试），后续实现
- `profiles/`：设备级/场景级配置，后续实现

## 首期目标

- `connection/providers/rp5-serial/`：Windows Host 独占物理串口 + WSL2 Client 三模式接入
- `workflows/boot-failure-debug-loop/` v1：后续计划实现

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

- 设计规格：`docs/specs/2026-06-19-loop-engineering-design.md`
- 实施计划：`docs/plans/2026-06-19-rp5-serial-host-client-mvp.md`

---
name: loop-engineering
description: loop engineering 总体工作流
---

# Loop Engineering Workflow

## 目标

由 AI 接管开发板，稳定执行「观察 → 分类 → 采样 → 诊断 → 报告」的调试闭环：

1. **观察**：通过连接层稳定接管设备输出（串口/ADB）。
2. **分类**：基于规则识别故障类型（无输出 / kernel panic / boot hang / 反复重启 等）。
3. **采样**：在只读或低风险动作范围内采集证据。
4. **诊断**：汇总证据给出分类与建议。
5. **报告**：输出人类可读与机器可读的诊断报告。

## 当前阶段

本目录当前为占位骨架，仅 `connection/providers/rp5-serial/` 进入实现阶段：

- rp5-serial provider MVP（Windows Host + WSL2 Client）正在实施
- 业务闭环 `workflows/boot-failure-debug-loop/` 尚未实现

## 后续实现的模块

以下模块标注为「后续实现」，当前不创建目录与代码：

- `core/`：loop 通用抽象（session / lease / event / rule / action / attempt 的跨 provider 抽象）
- `workflows/boot-failure-debug-loop/`：启动失败调试闭环
- `profiles/`：设备级/场景级配置

## 分层职责

- **connection/**：连接域，承载协议契约、provider profile、具体 provider 实现
- **workflows/**：业务闭环流程，消费 connection 提供的接入能力
- **core/**：loop 通用抽象，不绑定具体 provider

## 与 harness 的关系

loop bash 入口复用 harness observability，但不依赖 harness 业务 workflow。详见 `engineering/loop/README.md`。

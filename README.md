# AndroidSystemEnhance

Android 系统增强项目：为树莓派等平台提供**内核 + AOSP 系统级增强**，包含结构化日志打点（LcView）、USB 存储 IO 监控（LcIod）及配套故障注入/验证工具。

> **项目定位**：代码主导、文档辅助。`code/` 是核心资产（各平台增强代码归档），`docs/` 是给人看的说明文档，`harness/` 是配套开发机制。

## 目录结构

| 目录 | 职责 | 说明 |
|------|------|------|
| [`code/`](code/) | 系统增强代码归档（主导） | 对应 `~/workspace/` 编译源码树的精确镜像，平台目录见下表 |
| [`docs/`](docs/) | 技术文档（辅助，给人看） | 01-打点增强、02-IO增强、开发工具等 |
| [`harness/`](harness/) | 开发机制 | 路径配置、归档/同步/文档同步 workflow、规则、参考文档 |

### 平台与特性

| 平台 | 目录 | 增强内容 |
|------|------|---------|
| Raspberry Pi 5 | [`code/rpi5/`](code/rpi5/) | LcView 日志打点（内核/HAL/Daemon）、LcIod IO 监控（内核/HAL/Service）、USB 故障注入与验证 |
| Raspberry Pi Zero 2W | [`code/rpi-zero2w/`](code/rpi-zero2w/) | usb-fault-inject 协议级故障注入工具 |

## 开发工作流

源码调试在 `~/workspace/` 编译树进行，验证通过后归档到 `code/`：

1. **改源码**：在 `~/workspace/`（kernel / aosp）修改，编译、打包、上板验证
2. **归档**：`/sync-workspace-to-code` 将 workspace 已验证改动镜像到 `code/rpi5/`
3. **同步**：验证 NG 且无法修复时，`/sync-code-to-workspace` 以 promoted baseline 为真相源把 workspace 拉回一致

> 机制详见 [`harness/README.md`](harness/README.md)，源码改动纪律见 [`harness/rules/source-code-modify.md`](harness/rules/source-code-modify.md)。

## 文档入口

- 打点增强（LcView）：[`docs/01-打点增强/`](docs/01-打点增强/)
- IO 增强（LcIod）：[`docs/02-IO增强/`](docs/02-IO增强/)
- 源码阅读环境（VS Code / OpenGrok）：[`docs/development-tools.md`](docs/development-tools.md)

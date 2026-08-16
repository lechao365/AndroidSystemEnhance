# harness/reference — RPI5 开发参考文档

> **对象**: 主要供 LLM 在 RPI5 AOSP/内核开发、环境搭建、排障时查询，同时保持人类可读。
> **原则**: 命令模板可直接复制执行；每条硬性约束带规则 ID + 违反后果；保留必要的"为什么"讲解便于理解。
> **路径**: 所有路径均可被 `KERNEL_WS`/`AOSP_WS` 环境变量或 paths.conf 覆盖（见 `harness/config/paths.conf`）。

## 文档索引

| 文档 | 规则 ID | 加载时机 | 来源 |
|------|---------|---------|------|
| [env-setup-reference.md](env-setup-reference.md) | ENV-001~006 | 涉及 WSL2 / 宿主环境搭建、AOSP 编译前准备、ADB 工具接入 | 00.1 |
| [build-reference.md](build-reference.md) | BLD-001~012 | 涉及 RPI5 AOSP / 内核编译、源码获取、ccache、打包 | 00.3 + 脚本事实提取 |
| [flash-deploy-reference.md](flash-deploy-reference.md) | FLASH-001~007 | 涉及镜像写入 SD 卡、首次上电、建立 ADB/串口入口 | 00.4 + 00.2 |
| [incremental-dev-reference.md](incremental-dev-reference.md) | INC-001~010 | 涉及模块级修改、增量编译、镜像推送、内核替换、回退 | 00.5 |
| [debug-tools-reference.md](debug-tools-reference.md) | DBG-001~012 | 涉及日志抓取、串口调试、WSL 映射 USB 设备 | 00.6（调试部分）+ 00.2 |
| [remote-access-reference.md](remote-access-reference.md) | RMT-001~008 | 涉及跨网络远程访问 opencode WebUI（Tailscale + Serve） | 00.7 |

## 相关人类向文档

| 文档 | 用途 |
|------|------|
| [docs/development-tools.md](../../docs/development-tools.md) | VS Code + OpenGrok 源码阅读/搜索环境搭建（面向人类开发者，LLM 一般无需加载） |

## 使用建议

- 编译类任务：加载 `build-reference.md` + `incremental-dev-reference.md`。
- 部署类任务：加载 `flash-deploy-reference.md`。
- 排障类任务：先按问题域定位对应文档的"常见问题与排查"节。
- 环境搭建全流程：按 `env-setup → build → flash → debug → remote` 顺序参考。

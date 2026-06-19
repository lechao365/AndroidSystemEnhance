# Scripts

独立一次性脚本——不属于任何工作流闭环，通常是手动触发的构建或运维工具。

## 文件说明

- [`mk_rpi5_full_image.sh`](./mk_rpi5_full_image.sh) — 树莓派 5 AOSP 一键编译打包脚本。通过 `-mode` 参数选择构建范围（全量 / 仅打包 / 仅内核 / 仅 vendor / 仅 system），最终生成可刷写 SD 卡的 `.img`。

## 约定

- 脚本同样遵守 [script-observability.md](../rules/script-observability.md) 规范（source 公共库、结构化日志）。
- 与 `workflows/` 的区别：scripts 是单脚本工具，无多步确认闭环；workflows 是脚本 + AI 交互的完整流程。

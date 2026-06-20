# Scripts

独立一次性脚本——不属于任何工作流闭环，通常是手动触发的构建或运维工具。

## 文件说明

- [`mk_rpi5_full_image.sh`](./mk_rpi5_full_image.sh) — 树莓派 5 AOSP 一键编译打包脚本。通过 `-mode` 参数选择构建范围（全量 / 仅打包 / 仅内核 / 仅 vendor / 仅 system），最终生成可刷写 SD 卡的 `.img`。

### 静态校验器（validator）

harness 自身的文档 / 脚本 / 配置一致性静态校验入口，无副作用、只读扫描，退出码 `0`=全绿、`1`=有告警、`3`=环境错误。

- [`validate_harness_docs.sh`](./validate_harness_docs.sh) — 文档/契约层校验：README 导航链接存在性、各子目录 README 文件清单与实际目录一致性、`templates/*.md` 中 PlantUML `@startuml`/`@enduml` 配对闭合与花括号占位符、`workflows/*/WORKFLOW.md` front matter（含 `name`/`description`）。
  - 调用：`bash engineering/harness/scripts/validate_harness_docs.sh`
- [`validate_harness_scripts.sh`](./validate_harness_scripts.sh) — bash 脚本合规校验：`workflows/*/*.sh` 与 `scripts/*.sh` 是否 source `harness_bootstrap.sh`、是否调用 `harness_init`、是否出现裸 `exit` / 裸 `/tmp/` / 直接依赖 `_H_*`/`_h_*` 私有符号（公共库自身豁免）。
  - 调用：`bash engineering/harness/scripts/validate_harness_scripts.sh`
- [`validate_harness_config.sh`](./validate_harness_config.sh) — 配置层校验：`scope-mapping.yaml` / `doc-sync-mapping.yaml` 存在且可被 python3 解析、`version` 合法性、`priority` 为整数、`match` 非空、`scope` 命名规范、`mode` 值域、`routes[].docs` 项以 `docs/` 开头。依赖 `python3`（含 `yaml` 模块）。
  - 调用：`bash engineering/harness/scripts/validate_harness_config.sh`

## 约定

- 脚本同样遵守 [script-observability.md](../rules/script-observability.md) 规范（source 公共库、结构化日志）。
- 与 `workflows/` 的区别：scripts 是单脚本工具，无多步确认闭环；workflows 是脚本 + AI 交互的完整流程。

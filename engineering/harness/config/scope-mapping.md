# Git Commit Scope 映射

> **用途**：`git-push-to-server` workflow 依据本表，按改动目录识别 scope。
> 新增工程目录时只需更新本文件，无需修改 workflow。

## scope 判定规则

按改动行数最多目录为准，自上而下首条命中即归属：

| 目录特征 | 模块识别规则 | scope |
|---------|------------|-------|
| `kernel/` 下 `vendor/lechao/LcView/**` | 路径含 `LcView` | `kernel-lcview` |
| `kernel/` 下 `vendor/lechao/LcIod/**` | 路径含 `LcIod` | `kernel-lciod` |
| `kernel/` 其他 | 无明确模块 | `kernel-unknown` |
| `aosp/` 下涉及 lcview/lciod | grep 文件名/路径 | `aosp-lcview` / `aosp-lciod` |
| `aosp/` 其他 | 无明确模块 | `aosp-unknown` |
| `engineering/harness/workflows/` | 固定 | `workflows` |
| `engineering/harness/rules/` | 固定 | `rules` |
| `engineering/harness/config/` | 固定 | `config` |
| `engineering/harness/scripts/` | 固定 | `scripts` |
| `engineering/harness/templates/` | 固定 | `templates` |
| `docs/` | 固定 | `docs` |
| `.opencode/` | 固定 | `tooling` |
| 未命中 | 兜底 | `misc` |

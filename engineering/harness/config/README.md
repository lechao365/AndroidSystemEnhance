# Config

workflow 依赖的映射配置表——把"目录特征 → scope / 文档归属"的规则抽成独立数据源，新增目录或模块时只改本目录配置，不动 workflow 脚本。

## 文件说明

| 文件 | 作用 | 被谁引用 |
|------|------|---------|
| [scope-mapping.md](./scope-mapping.md) | Git commit 的 scope 判定规则：按改动行数最多目录映射到 scope 词（如 `kernel-lcview`） | `workflows/git-push-to-server/` |
| [doc-sync-mapping.md](./doc-sync-mapping.md) | patchs → 技术文档的精准分发规则：按路径 glob 匹配分发到 `01-*` / `02-*` 文档目录 | `workflows/sync-patchs-to-doc/` |

## 何时更新

- **新增工程目录**：在 `scope-mapping.md` 追加 scope 映射行
- **新增特性文档目录**（如 `03-*`）：在 `doc-sync-mapping.md` 追加 patchs 路径特征 → 文档目录的映射
- 两份配置均采用"自上而下首条命中即归属"的匹配规则，新增条目注意优先级顺序

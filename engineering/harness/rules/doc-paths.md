# 文档归档路径（覆盖 Superpowers Skill 默认）

> **规则 ID**：`DOC-001`
> - `DOC-001`：过程型文档（spec/plan）与长期技术文档分层；spec 与 plan 必须落到 `docs/specs/`、`docs/plans/`，禁止使用 superpowers 默认的 `docs/superpowers/` 路径。

> **指令优先级**：本规则优先级高于 superpowers skill 内的硬编码路径，所有 skill 必须遵守。

## 路径映射

Superpowers 的 brainstorming、writing-plans 等 skill 默认将文档写入 `docs/superpowers/` 下，本项目统一覆盖为 `docs/` 根下：

| Skill 默认路径 | 项目实际路径 | 触发 skill |
|----------------|-------------|-----------|
| `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` | `docs/specs/YYYY-MM-DD-<topic>-design.md` | brainstorming |
| `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md` | `docs/plans/YYYY-MM-DD-<feature-name>.md` | writing-plans |

## 规则

1. 所有设计规格和实施计划必须保存到上表中的"项目实际路径"。
2. 禁止创建 `docs/superpowers/` 目录或其下的任何文件。
3. 若发现 skill 自动创建了 `docs/superpowers/` 路径的文件，应立即移动到正确的 `docs/specs/` 或 `docs/plans/` 并删除残留目录。

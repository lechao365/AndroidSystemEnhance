# Patchs → Doc 同步设计

## 背景

项目已有两条同步链路：

1. **Code → Patchs**：`sync_code_to_patchs.sh` 将 workspace 源码归档到 `patchs/rpi5/`
2. **Patchs → README 映射表**：手动更新 `patchs/rpi5/README.md` 文件映射表

本设计新增第三条链路：

3. **Patchs → 技术文档**：`sync_patchs_to_doc.sh` 生成变动报告，AI 增量更新技术文档

## 整体工作流

```
链路 1 (已有): ~/workspace/ 源码 → patchs/rpi5/ 归档        (sync_code_to_patchs.sh)
链路 2 (已有): patchs/rpi5/ → README.md 文件映射表          (手动，保持现状)
链路 3 (新增): patchs/rpi5/ → 技术文档(01-xx/02-xx)          (sync_patchs_to_doc.sh + AI)
```

链路 3 流程：

1. 用户在 workspace 改代码后，先执行 `sync_code_to_patchs.sh` 完成归档（链路 1）
2. 用户手动触发 `sync_patchs_to_doc.sh`
3. 脚本对比 `patchs/rpi5/` 相对于 git HEAD 的 diff，输出结构化变动报告
4. AI 读取报告，自行判断需要检查/更新哪些技术文档，给出更新方案
5. 用户确认方案后，AI 执行文档落盘

**触发时机**：完全由用户手动触发，不做自动同步。

## 产出物清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `rules/sync_patchs_to_doc.md` | 规则文档 | 约束 patchs→doc 同步的行为规范 |
| `scripts/sync_patchs_to_doc.sh` | 脚本 | 生成 patchs/rpi5/ 变动报告，供 AI 消费 |
| `AGENTS.md` | 修改 | 新增 `## 文档同步规则` 章节 |

## rules/sync_patchs_to_doc.md 规则文档

**职责**：约束 patchs→doc 同步的行为规范，不定义映射表。

**内容结构**：

| 章节 | 说明 |
|------|------|
| 适用范围 | 约束 `patchs/rpi5/` 变动到技术文档的同步行为 |
| 一键同步 | 给出脚本命令 `bash scripts/sync_patchs_to_doc.sh` |
| 参与同步的文件类型 | 明确哪些 patchs 变动类型触发文档更新（new/modified/delete） |
| 同步流程 | 脚本输出变动报告 → AI 读取报告 → 给出文档更新方案 → 用户确认 → AI 落盘 |
| AI 更新约束 | AI 必须先输出文档更新方案（具体改哪些文件、改什么内容），经用户确认后方可落盘。禁止未经确认直接修改文档。AI 以脚本输出的变动报告为输入，自行判断影响的文档范围，必要时可读取全量代码。 |
| 不涉及的文档 | `patchs/rpi5/README.md` 文件映射表的更新仍走链路 1（保持现状） |

## scripts/sync_patchs_to_doc.sh 脚本

**职责**：生成 `patchs/rpi5/` 相对于 git HEAD 的结构化变动报告，供 AI 消费。脚本本身不触碰任何文档文件。

**核心逻辑**：

1. 在仓库根目录执行 `git diff HEAD --stat -- patchs/rpi5/`
2. 解析输出，按以下维度分组：
   - 变动类型：A(新增) / M(修改) / D(删除) / R(重命名)
   - 目录分组：`kernel/modified/`、`kernel/new/`、`aosp/modified/`、`aosp/new/`、`others/`
3. 对每个变动文件，输出文件路径、变动类型、+- 行数统计
4. 支持 `--check-only` 参数（仅输出报告，提示用户需手动触发 AI 更新）

**输出格式示例**：

```
=== Patchs → Doc 变动报告 ===
基准: HEAD (abc1234)

--- kernel/new/vendor/lechao/LcView/ ---
  [A] builder.c      +120 -0
  [M] ring.c          +15 -3

--- aosp/new/vendor/lechao/services/lechao_lcview/ ---
  [M] hal.cpp          +8 -2

总计: 3 个文件变动 (2 新增, 1 修改)
```

**设计决策**：

- **不内置映射逻辑**：输出纯差分列表，由 AI 自行判断影响的文档范围
- **不输出完整 diff 内容**：只输出 `--stat` 级别的摘要，AI 需要时可自行读取具体文件
- **不涉及文档写入**：脚本只负责生成报告，文档更新由 AI 完成

## AGENTS.md 新增章节

在现有 `## 同步与归档规则` 之后、`## 源码改动优先级` 之前，新增：

```markdown
## 文档同步规则

当 patchs/rpi5/ 发生变动后，执行 `scripts/sync_patchs_to_doc.sh` 生成变动报告，
AI 根据报告内容给出技术文档的更新方案，经确认后落盘。
规则详见 [rules/sync_patchs_to_doc.md](rules/sync_patchs_to_doc.md)。
```

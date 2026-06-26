# harness workflow lc- 前缀统一改名设计

| 项目 | 值 |
|------|-----|
| 文档状态 | Draft |
| 创建日期 | 2026-06-26 |
| 作者 | AI 辅助生成 |
| 关联规则 | SRC-001, OBS-001, OBS-002, PATH-001, PAR-001 |
| 关联工作流 | git-push-to-server, sync-code-to-patchs, revert-code-from-patchs, sync-patchs-to-doc, lc-quick-fix-issue |

## 1. 背景与动机

当前 `engineering/harness/workflows/` 下共 5 个 workflow，命名风格不一致：

- `lc-quick-fix-issue` — 已使用 `lc-` 前缀
- `git-push-to-server`、`sync-code-to-patchs`、`revert-code-from-patchs`、`sync-patchs-to-doc` — 无 `lc-` 前缀

目标是把后 4 个统一改为 `lc-` 前缀，形成一套独有的工作流命名体系。

## 2. 目标

- **命名统一**：4 个 harness workflow 目录、命令、文档引用全部加 `lc-` 前缀
- **零行为变更**：脚本逻辑、`harness_init` 注册名、workflow 行为完全不变
- **零残留**：活文档区（harness、commands、patchs）旧名零残留
- **可验证**：改名正确性由现有 5 层验证体系保障，全绿即证明无遗漏

## 3. 非目标（YAGNI）

- 不改 `docs/specs/`、`docs/plans/` 历史文档（设计快照，记录当时状态）
- 不改脚本文件名（`sync_code_to_patchs.sh` 等下划线变体，与 workflow 名是两套独立命名空间）
- 不改 `harness_init` 注册名（脚本名不变则注册名不变）
- 不动 `lcview-adb-run`（属 loop 体系，不在 harness/workflows 下）
- 不改 `patchs/rpi5/manifest.yaml`（注释引用脚本名，脚本名不变）

## 4. 改名映射

| 旧名 | 新名 |
|------|------|
| `git-push-to-server` | `lc-git-push-to-server` |
| `sync-code-to-patchs` | `lc-sync-code-to-patchs` |
| `revert-code-from-patchs` | `lc-revert-code-from-patchs` |
| `sync-patchs-to-doc` | `lc-sync-patchs-to-doc` |

## 5. 改动范围

### 5.1 目录重命名（`git mv`，4 个）

```
engineering/harness/workflows/git-push-to-server        → lc-git-push-to-server
engineering/harness/workflows/sync-code-to-patchs       → lc-sync-code-to-patchs
engineering/harness/workflows/revert-code-from-patchs   → lc-revert-code-from-patchs
engineering/harness/workflows/sync-patchs-to-doc        → lc-sync-patchs-to-doc
```

### 5.2 命令文件重命名（`git mv`，4 个）

```
.opencode/commands/git-push-to-server.md        → lc-git-push-to-server.md
.opencode/commands/sync-code-to-patchs.md       → lc-sync-code-to-patchs.md
.opencode/commands/revert-code-from-patchs.md   → lc-revert-code-from-patchs.md
.opencode/commands/sync-patchs-to-doc.md        → lc-sync-patchs-to-doc.md
```

### 5.3 命令文件内容（4 个 `.md`）

每个文件改 2 处路径引用（脚本路径 + WORKFLOW.md 路径）：
- `!bash engineering/harness/workflows/<旧名>/xxx.sh` → `<新名>`
- `@engineering/harness/workflows/<旧名>/WORKFLOW.md` → `<新名>`

### 5.4 workflow 内文件（每个 workflow 的 `WORKFLOW.md` + `README.md` + 脚本）

对 4 个被改名的 workflow，每个 workflow 内部：
- `WORKFLOW.md`：front matter `name:` 字段、一级标题、用法示例里的目录路径
- `README.md`：标题、命令名（`/旧名` → `/lc-新名`）、引用其他 workflow 的交叉引用
- 脚本：用法 echo、规则路径引用里的目录名（脚本文件名保持下划线不变）

### 5.5 索引/导航活文档

| 文件 | 改动点 |
|------|--------|
| `engineering/harness/workflows/README.md` | workflow 清单表（4 行）+ 用法示例路径 |
| `engineering/harness/README.md` | 快速导航 + 真相源矩阵 |
| `engineering/harness/config/README.md` | 4 处「workflows/<旧名>/（消费）」 |
| `engineering/harness/rules/source-code-modify.md` | SRC-002 引用 `sync-code-to-patchs` |
| `engineering/harness/rules/README.md` | 1 处引用 |
| `engineering/harness/templates/README.md` | 「被 sync-patchs-to-doc 消费」 |
| `engineering/harness/templates/engineering-readme-template.md` | 「被 sync-patchs-to-doc」 |

### 5.6 测试文件与夹具

| 文件 | 改动点 |
|------|--------|
| `engineering/harness/tests/test_sync_code_to_patchs.sh` | `SYNC_SCRIPT` 路径（:8）、`run_sync` 调用路径（:66）→ `lc-sync-code-to-patchs/` |
| `engineering/harness/tests/test_revert_code_from_patchs.sh` | `mkdir` 路径（:56）、`cp` 源路径（:60-61）、`run_revert_script` 调用路径（:99）→ `lc-revert-code-from-patchs/` |
| `engineering/harness/tests/README.md` | fixtures 目录说明 |
| `engineering/harness/tests/fixtures/sync-code-to-patchs/` | `git mv` → `lc-sync-code-to-patchs/` |
| `engineering/harness/tests/fixtures/revert-code-from-patchs/` | `git mv` → `lc-revert-code-from-patchs/` |

### 5.7 其他活文档 + 跨 workflow 引用

| 文件 | 改动点 |
|------|--------|
| `patchs/rpi5/README.md` | 命令名 + 上下游依赖说明（多处） |
| `engineering/harness/workflows/lc-quick-fix-issue/WORKFLOW.md` | Stage 7 调用路径 `../git-push-to-server/commit_and_push.sh` → `../lc-git-push-to-server/`（:189, :211, :230） |
| `engineering/harness/workflows/lc-quick-fix-issue/README.md` | 引用 `git-push-to-server/commit_and_push.sh`（:10, :44） |

## 6. 明确不改

- `docs/specs/`、`docs/plans/` 全部历史文档（设计快照）
- 脚本文件名（`sync_code_to_patchs.sh` 等下划线变体）与 `harness_init` 注册名
- `patchs/rpi5/manifest.yaml`（注释引用脚本名，脚本名不变）
- `lcview-adb-run`（loop 体系，不属 harness）
- `engineering/output/log/` 运行时产物

## 7. 测试验证设计（5 层）

改名是纯标识符替换，无逻辑变更。正确性由以下 5 层验证保障，全部 PASS 即证明改名无遗漏、无破坏。

### 7.1 第 1 层：静态校验脚本（自动生效，无需改动校验脚本自身）

| 校验脚本 | 如何覆盖本次改名 |
|---------|-----------------|
| `validate_harness_docs.sh` Step 1 | **核心防线**。扫描所有 README.md 的 Markdown 相对路径链接，校验目标文件存在。任何 `workflows/<旧名>/` 残留链接 → 报 `MISS`。 |
| `validate_harness_docs.sh` Step 2 | 子目录 README 文件清单与实际目录一致性。改名后目录文件清单必须与 README 登记一致。 |
| `validate_harness_docs.sh` Step 4 | 校验 `workflows/*/WORKFLOW.md` 的 YAML front matter `name:`/`description:` 字段完整性。改名后 `name:` 必须同步，这层兜底。 |
| `validate_harness_scripts.sh` | 扫描 `$HARNESS_DIR` 全树 bash 脚本。脚本内容不变（仍 source bootstrap + harness_init），路径变化不影响判定。 |

这 4 个校验脚本本身**不需要改动**——它们按目录树自适应扫描，旧目录消失后自动扫描新目录。

### 7.2 第 2 层：功能测试（测试文件必须改，否则直接挂）

| 测试文件 | 硬编码点 | 改动 |
|---------|---------|------|
| `test_sync_code_to_patchs.sh` | `:8` `SYNC_SCRIPT` 路径、`:66` `run_sync` 调用路径 | → `lc-sync-code-to-patchs/` |
| `test_revert_code_from_patchs.sh` | `:56` `mkdir` 路径、`:60-61` `cp` 源路径、`:99` 调用路径 | → `lc-revert-code-from-patchs/` |

这两个测试是**真实执行脚本**的端到端验证（含 git 仓库初始化、fixture 注入、输出断言），是改名正确性的最强保证。改名后必须 `PASS`。如果路径改漏了，测试第一步就挂。

### 7.3 第 3 层：残留扫描（改名专属，一次性）

活文档区旧名必须零残留（`docs/specs`、`docs/plans` 除外）：

```bash
rg -n "workflows/(git-push-to-server|sync-code-to-patchs|revert-code-from-patchs|sync-patchs-to-doc)\b" \
   engineering/harness engineering/loop .opencode patchs AGENTS.md
# 预期：0 命中
```

同时扫描命令名形式（用于文档中 `/旧名` 引用）：

```bash
rg -n "/(git-push-to-server|sync-code-to-patchs|revert-code-from-patchs|sync-patchs-to-doc)\b" \
   engineering/harness .opencode patchs AGENTS.md
# 预期：0 命中
```

### 7.4 第 4 层：命令可达性

`.opencode/commands/lc-*.md` 里 `!bash` 和 `@` 引用的路径必须指向新目录。校验每个新命令文件的引用目标存在：

```bash
# 对 4 个新命令文件，抽取引用路径并 test -f
for cmd in lc-git-push-to-server lc-sync-code-to-patchs lc-revert-code-from-patchs lc-sync-patchs-to-doc; do
  grep -E '^\s*[!@]' ".opencode/commands/$cmd.md" | while read -r line; do
    # 解析出路径，test -f 验证
    ...
  done
done
```

### 7.5 验证执行顺序（实施后按此跑，全绿即完成）

1. `bash engineering/harness/tests/test_sync_code_to_patchs.sh` → 期望 `PASS: test_sync_code_to_patchs`
2. `bash engineering/harness/tests/test_revert_code_from_patchs.sh` → 期望 `PASS: test_revert_code_from_patchs.sh`
3. `bash engineering/harness/scripts/validate_harness_scripts.sh` → 期望 `verdict=PASS`
4. `bash engineering/harness/scripts/validate_harness_docs.sh` → 期望 `verdict=PASS`（**最关键**，覆盖链接/front matter/文件清单一致性）
5. 第 3 层残留扫描 → 期望 0 命中
6. 第 4 层命令可达性 → 期望全部存在

**5 层全绿即证明改名无遗漏。** 任一层失败即定位到具体遗漏点。

## 8. 风险与回滚

- **风险极低**：纯标识符替换，无逻辑变更。脚本内部行为完全不变。
- **回滚成本**：`git revert` 单次提交即可回到改名前状态（所有改动通过 `git mv` + 文件编辑，单次提交）。
- **唯一需注意**：`lc-quick-fix-issue` 的 Stage 7 调用了 `../git-push-to-server/commit_and_push.sh`，改名后必须同步改这个跨 workflow 引用路径，否则 `lc-quick-fix-issue` 会挂。已纳入 5.7。

## 9. 实施顺序

1. `git mv` 4 个 workflow 目录 + 4 个命令文件 + 2 个 fixtures 目录（10 次 `git mv`）
2. 批量改文件内容（命令文件 4 个、workflow 内文件、索引文档、测试文件、patchs README、lc-quick-fix-issue 引用）
3. 按 §7.5 顺序跑 6 步验证，全绿则完成
4. 单次 commit，message: `refactor(harness): unify workflow naming with lc- prefix`

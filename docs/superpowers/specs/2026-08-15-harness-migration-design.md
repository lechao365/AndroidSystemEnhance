# Harness 能力迁回 AndroidSystemEnhance 设计文档

> 日期：2026-08-15
> 状态：已确认（用户逐项确认）
> 目标：把之前剥离到 LcHarness 的 harness 能力迁回本项目，去掉 LcHarness 复杂通用机制，绝对内聚。

## 1. 背景与目标

早期 AndroidSystemEnhance 将 `engineering/harness`、`engineering/loop`、`.opencode/commands` 剥离到独立 LcHarness 仓（commit `be4813a`）。由于 LcHarness 框架过重（投影、RID 规则、catalog/registry、多 pack 分层等）导致演进效率低下，现决定把与当前项目使用匹配的能力迁回，并在本项目内绝对内聚，不再依赖 LcHarness。

**目标**：
- 迁回 3 个 patchs 工作流（sync / revert / doc）及配套规则、配置、参考文档
- 去除 LcHarness 复杂通用能力（投影/RID/catalog/registry/packs/LE 框架）
- 本项目内创建 `harness/` 目录，所有 harness 相关内容内聚其中
- AGENTS.md 从 LcHarness 路径引用改为项目内 `harness/` 路径
- 不迁回：LE 框架（loop-engineering）、git-works pack、validators/cases/tests 通用测试机制

## 2. 已确认决策

| 决策点 | 结论 |
|--------|------|
| 迁移范围 | 3 个工作流 skill + 配套（rules / config / reference / apply_preset_bugs） |
| 依赖处理 | 项目内建 `harness/` 目录绝对内聚，不依赖 LcHarness |
| 路径配置 | 配置文件 + 环境变量覆盖（paths.conf + KERNEL_WS/AOSP_WS） |
| 目录结构 | 对齐 LcHarness profile 分层（lib / skills / config / rules / reference / scripts） |
| baseline 机制 | 保留轻量晋升登记（revert 前置 SRC-004 检查） |
| opencode 命令 | 保存到 `.opencode/command/`（opencode 原生命令发现路径） |
| pytest.ini | 删除（指向已失效的 engineering/output） |
| doc-sync 目标目录 | 改为实际目录 `01-打点增强/`、`02-IO增强/` |

## 3. 目标目录结构

```
AndroidSystemEnhance/
├── harness/                        # 新建，harness 能力全部内聚
│   ├── README.md                   # 使用说明（入口总览）
│   ├── lib/
│   │   ├── harness_lib.py          # 精简合并：log/step/exit/bootstrap（原 core 3 库精简）
│   │   └── paths.py                # 精简路径工具：读 paths.conf + 环境变量覆盖
│   ├── config/
│   │   ├── paths.conf              # PATCHS_DIR / KERNEL_WS / AOSP_WS（env 可覆盖）
│   │   ├── git_workspace_util.py   # 排除正则（sync/revert 共享）
│   │   ├── baseline-status.yaml    # 迁移现有 2 条 promoted 记录
│   │   ├── baseline-evidence-template.yaml
│   │   └── doc-sync-mapping.yaml   # 目标目录改为 01-打点增强/ 02-IO增强/
│   ├── skills/
│   │   ├── lc-harness-sync-code-to-patchs/{SKILL.md, lc_harness_sync_code_to_patchs.py}
│   │   ├── lc-harness-revert-code-from-patchs/{SKILL.md, lc_harness_revert_code_from_patchs.py}
│   │   └── lc-harness-sync-patchs-to-doc/{SKILL.md, lc_harness_sync_patchs_to_doc.py}
│   ├── rules/
│   │   ├── source-code-modify.md   # SRC-001~004（路径引用改 harness/ 内）
│   │   └── cxx-coding-rules.md     # CXX-001~004
│   ├── reference/
│   │   └── build-reference.md
│   ├── scripts/
│   │   └── apply_preset_bugs.py
├── .opencode/command/              # 新增：opencode 原生命令注册（3 个 .md）
├── 01-打点增强/ 02-IO增强/   # 文档，不变
├── docs/                           # 保留（specs 等）+ development-tools.md（人类向开发工具指南）
├── patchs/                         # 归档，不变
├── AGENTS.md                       # 更新：LcHarness 引用 → harness/ 内路径
└── pytest.ini                      # 删除
```

## 4. 脚本改造要点

### 4.1 依赖替换（3 个 skill 脚本 + apply_preset_bugs）

| 原依赖（LcHarness） | 改为 |
|---------------------|------|
| `sys.path.insert(0, ...core/lib/python...)` | 删除，改为 `harness/` 锚点定位 |
| `from harness_bootstrap import harness_init, harness_exit` | `from harness.lib.harness_lib import harness_init, harness_exit` |
| `from harness_observability import log_info, log_warn, log_error, step_begin, step_end` | `from harness.lib.harness_lib import ...` |
| `from harness_path_util import path, env_path, repo_root` | `from harness.lib.paths import path, env_path, repo_root` |
| `from resolve_conf_refs import parse_conf, resolve_refs` | 由 `harness.lib.paths` 内部消化 |
| `from local_paths import path as profile_path, profile_config_dir` | `from harness.config.paths import path as profile_path, config_dir` |
| `from git_workspace_util import is_excluded, ...` | `from harness.config.git_workspace_util import ...` |

### 4.2 harness/lib/harness_lib.py

合并原 `harness_bootstrap` + `harness_observability` 精简版，提供：
- `harness_init(name)` / `harness_exit(code)`（UTF-8、退出码汇总、excepthook）
- `log_info` / `log_warn` / `log_error`
- `step_begin` / `step_end`

去掉：catalog/registry/packs 发现、制品归档轮转、环境探测等 LcHarness 通用机制。

### 4.3 harness/lib/paths.py

合并原 `harness_path_util` + `resolve_conf_refs` + `local_paths` 精简版：
- `repo_root()`：从 `__file__` 向上找 `harness/` 目录锚点
- `path(key)`：读 `harness/config/paths.conf`，相对路径基于项目根解析
- `env_path(key)`：支持环境变量覆盖
- `config_dir()`：返回 `harness/config/`

### 4.4 harness/config/paths.conf

```conf
# harness 路径配置（单一事实源）
# 相对路径基于项目根解析；KERNEL_WS/AOSP_WS 支持环境变量覆盖
PATCHS_DIR="patchs/rpi5"
KERNEL_WS="${KERNEL_WS:-}"
AOSP_WS="${AOSP_WS:-}"
```

### 4.5 SKILL.md 精简

- 路径引用 `${LCHARNESS_ROOT}/profiles/android-system-enhance/...` → `harness/skills/...`
- 去掉 LcHarness 通用规则 ID（OBS-XXX / LAY-XXX / XPLAT / SRC 之外的通配引用），仅保留项目内规则（SRC-001~004、CXX-001~004）
- 保留：触发条件、前置条件、退出码表、人工确认门、状态标记表、失败恢复、工作流步骤
- 脚本调用路径改为 `python3 harness/skills/<name>/<name>.py`

### 4.6 revert 脚本

- 保留 `_check_baseline_promoted()`（读 `harness/config/baseline-status.yaml`）
- baseline-status.yaml 迁移现有 2 条 promoted 记录（BL-20260622-01 / BL-20260624-01）

### 4.7 apply_preset_bugs.py

- 替换依赖为 harness.lib，路径解析改为 `paths.env_path("AOSP_WS")`
- 保留 3 个预设 bug 逻辑与 revert 能力

## 5. AGENTS.md 更新

| 当前引用（LcHarness 绝对路径） | 改为 |
|-------------------------------|------|
| `core/rules/source-code-modify.md` | `harness/rules/source-code-modify.md` |
| `core/rules/cxx-coding-rules.md` | `harness/rules/cxx-coding-rules.md` |
| `profiles/android-system-enhance/reference/build-reference.md` | `harness/reference/build-reference.md` |
| `profiles/android-system-enhance/config/baseline-status.yaml` | `harness/config/baseline-status.yaml` |
| `core/rules/parallel-strategy.md` | 移除（LcHarness 通用规则不再适用） |
| `core/rules/plantuml.md` | 精简内嵌到 `harness/rules/plantuml.md`（保留画图约束） |
| `core/rules/script-observability.md` | 移除（脚本已精简，observability 内嵌 harness_lib） |
| `core/rules/path-management.md` | 移除（路径管理内嵌 paths.conf + paths.py） |
| `core/rules/manifest.yaml` 准入查询 | 移除（LcHarness 控制面机制） |
| LcHarness 控制面 alias（lc-attach 等 6 条） | 移除 |

新增/保留章节：
- "Harness 命令"章节：3 个 slash command 用法说明
- 文件删除规则、测试防护（`make lechao_lcview_unit_test...`）保留

## 6. opencode 命令注册

`.opencode/command/` 下新建 3 个命令文件（opencode 原生发现路径）：

- `.opencode/command/lc-harness-sync-code-to-patchs.md`
- `.opencode/command/lc-harness-revert-code-from-patchs.md`
- `.opencode/command/lc-harness-sync-patchs-to-doc.md`

内容：description frontmatter + 调用 `python3 harness/skills/<name>/<name>.py $ARGUMENTS` + 遵循完整工作流（引用对应 SKILL）。

## 7. 删除项

| 项 | 处理 |
|----|------|
| `pytest.ini` | 删除（指向已失效的 engineering/output） |

## 8. doc-sync-mapping.yaml 调整

目标目录从 `docs/01-打点增强`、`docs/02-IO增强` 改为实际文档目录 `01-打点增强/`、`02-IO增强/`（映射规则与优先级保持不变）。

## 9. 不迁回项（YAGNI）

- LE 框架（loop-engineering：状态机/五道闸/串口/部署）
- LE 配套配置：`config/target-paths.yaml`（补丁白名单，供 LE patch_applier 校验）、`config/patch_knowledge_base.json`（LE kb_analyzer 知识库）、`capability-spec.yaml`（LE spec ID 注册表）、`negative-samples.yaml`
- git-works pack（push/branch-merge/branch-sync，已在 opencode skill 层独立存在）
- LcHarness core 控制面（attach/inject/status/detach/validate/reconcile）
- validators / cases / tests / 通用测试机制
- 投影（projection）、RID 规则、catalog/registry、packs 分层、能力目录（capability-registry）机制
- LcHarness core 规则：script-observability（OBS-*）、layering-boundaries（LAY-*）、xplat-rules（XPLAT-*）、path-management、skill-workflow、quality-gate 等（迁移后项目中不保留这些规则 ID）

## 10. LcHarness 特有机制清除清单

迁移时逐项核查，确保 `harness/` 内不再存在以下仅 LcHarness 特有的机制：

| # | LcHarness 特有机制 | 迁移处理 |
|---|-------------------|---------|
| 1 | `harness_bootstrap.harness_init` 内部 `bootstrap_pythonpath()`（扫描 packs/*/pack.yaml） | 去掉，harness_lib 不再做包扫描，仅做 UTF-8/退出码/excepthook |
| 2 | `harness_observability` 的 `_ensure_artifacts/_ensure_log`（读 LOG_DIR 键 + 制品轮转） | 去掉制品归档与轮转，log 仅 stderr（可选落盘 harness/log/） |
| 3 | `harness_path_util` 的 catalog 发现、`collect_pack_python_paths`、TEST_SANDBOX_DIR / HOST_STATE_DIR / REGISTRY 等键 | 去掉，paths.py 仅保留 paths.conf 读取 |
| 4 | `resolve_conf_refs` 的 `${...}` 跨引用机制 | 简化：仅保留环境变量展开（`${VAR:-default}`） |
| 5 | `local_paths.py`（profile.yaml 锚点 + LAY-002 分层） | 合并进 paths.py，删除独立文件 |
| 6 | 脚本注释中的规则 ID（`XPLAT-002`、`B1-1`/`B2-3`/`B3-1`/`B7-2`/`B7-3`/`B9-1`、`OBS-001/002`、`LAY-002`） | 全部清理，仅保留 SRC-001~004 / CXX-001~004 |
| 7 | SKILL.md 中 `Related policy IDs` 引用的 OBS/DOC 规则 | 清理，仅保留 SRC/CXX 项目内规则 |
| 8 | SKILL.md 中 `${LCHARNESS_ROOT}` / `profiles/android-system-enhance/` 路径 | 改为 `harness/skills/...` |
| 9 | `profile.yaml`（投影声明）/ `adapters/`（opencode 命令适配） | 不迁，命令改放 `.opencode/command/` |
| 10 | `LOG_DIR` / `artifacts` 归档路径约定（`output/log/<script>/artifacts/`） | 去掉；产物仅保留脚本 stdout + 可选 `harness/log/` |

> 说明：`git_workspace_util.py`（排除正则）本身是通用工具，保留功能，仅清理注释中的 B2-3/B3-1/LAY-002 引用。

## 11. 实施步骤

1. 创建 `harness/` 目录骨架（lib / config / skills / rules / reference / scripts）
2. 编写 `harness/lib/harness_lib.py` 与 `harness/lib/paths.py`
3. 编写 `harness/config/paths.conf`、`git_workspace_util.py`，迁移 baseline-status / evidence-template / doc-sync-mapping（改目录）
4. 迁移 3 个 skill（SKILL.md 精简 + 脚本依赖替换 + 脚本路径改造 + 按第 10 节清除清单清理规则 ID 注释）
5. 迁移 rules（source-code-modify / cxx-coding-rules，修正路径引用）+ reference（build-reference）
6. 迁移 scripts/apply_preset_bugs.py（依赖替换）
7. 创建 `.opencode/command/` 3 个命令文件
8. 更新 AGENTS.md（引用改 harness/ 内 + 移除 LcHarness 机制 + 新增 harness 命令章节）
9. 删除 pytest.ini
10. 验证：脚本 `--help` / `--check-only` 冒烟、Python 语法检查、规则文档引用一致性、按第 10 节清单逐项 grep 确认无 LcHarness 机制残留

## 12. 验收标准

- 3 个脚本可在项目根直接运行（不依赖 LcHarness 仓）
- `python3 harness/skills/lc-harness-sync-code-to-patchs/lc_harness_sync_code_to_patchs.py --check-only` 可执行（环境缺失时给出明确报错）
- AGENTS.md 不再含任何 `/mnt/d/Code/Github/LcHarness/` 路径引用
- 所有脚本无未解析的 LcHarness import
- 全仓 grep 无 `XPLAT-` / `OBS-` / `LAY-` / `B\d+-\d+` / `LCHARNESS_ROOT` / `profiles/android-system-enhance` 残留（SRC-* / CXX-* 除外）

# LcHarness Phase 3 专项搬迁与架构成型

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AndroidSystemEnhance 中的 `engineering/harness/` + `engineering/loop/` + `.opencode/commands/` 按 core/packs/profiles/adapters 分层搬迁到独立 LcHarness 仓，完成架构成型。

**Architecture:** Source 在 `AndroidSystemEnhance`，Target 在 `/mnt/d/Code/Github/LcHarness`（空仓，仅有 .git）。搬迁为 `cp -r`（不做 git mv，不保留历史）。搬迁中同步修正：所有脚本的 source 路径、硬编码路径、harness-paths.conf、lcview_analyzer_rules 重命名。搬迁后 ASE 残留的 `engineering/` 目录待用户确认后删除。

**Tech Stack:** Bash, cp, sed, git

---

## 文件路径总览

### Source（AndroidSystemEnhance 现有结构）

```
engineering/
├── harness/
│   ├── config/        (7 files: paths.conf, layer-map.yaml, scope-mapping.yaml, doc-sync-mapping.yaml, baseline-status.yaml, baseline-evidence-template.yaml, README.md)
│   ├── control-plane/ (7 files: lc-{attach,inject,detach,validate,status,reconcile,repo-registry}.sh + README.md)
│   ├── lib/           (shell: 3 files, python: 1 + __pycache__, bat: 1, README.md)
│   ├── reference/     (4 files: architecture.md, blueprint.md, build-reference.md, README.md)
│   ├── rules/         (9 files: manifest.yaml + 8 *.md)
│   ├── scripts/       (13 files: validate_*.sh x7, run_all_validations.sh, apply_preset_bugs.sh, check_access.sh, mk_rpi5_full_image.sh, start-opencode-server.sh, README.md)
│   ├── templates/     (6 files: 5 templates + README.md)
│   ├── tests/         (12 test .sh + fixtures/20 files + README.md)
│   ├── workflows/     (5 subdirs: lc-sync-code-to-patchs, lc-revert-code-from-patchs, lc-quick-fix-issue, lc-git-push-to-server, lc-sync-patchs-to-doc + README.md)
│   ├── tmp/           (空，不搬迁)
│   └── README.md
├── loop/              (~100 files: core/python/, deploy/python/, contracts/python/, controller/python/, connection/, config/3, scripts/4, cases/7, templates/1, workflows/1 + README.md x3 + WORKFLOW.md)
├── output/            (不搬迁，运行时产物)
└── README.md
.opencode/commands/    (6 files: le.md, lc-sync-code-to-patchs.md, lc-revert-code-from-patchs.md, lc-git-push-to-server.md, lc-quick-fix-issue.md, lc-sync-patchs-to-doc.md)
AGENTS.md              (含 LcHarness 别名)
docs/plans/            (3 lcharness plans)
docs/specs/            (1 lcharness spec)
```

### Target（LcHarness 新结构）

```
core/
├── lib/shell/    (harness_bootstrap.sh, harness_observability.sh, harness_path_util.sh)
├── lib/python/   (harness_path_util.py, 不含 __pycache__)
├── lib/bat/      (harness_path_util.bat)
├── lib/README.md
├── config/       (harness-paths.conf, lcharness-layer-map.yaml, README.md)
├── control-plane/(lc-{attach,inject,detach,validate,status,reconcile,repo-registry}.sh, README.md)
├── rules/        (manifest.yaml + 8 *.md)
└── scripts/      (仅 check_access.sh)

packs/
├── harness-validators/ (validate_*.sh x7, run_all_validations.sh, scope-mapping.yaml, pack.yaml, README.md)
├── harness-tests/      (test_*.sh x12, run_all_tests.sh, fixtures/, pack.yaml, README.md)
├── git-workflows/      (workflows/lc-git-push-to-server/, adapters/opencode/commands/lc-git-push-to-server.md, pack.yaml)
├── doc-governance/     (workflows/lc-sync-patchs-to-doc/, templates/, doc-sync-mapping.yaml, adapters/opencode/commands/lc-sync-patchs-to-doc.md, pack.yaml)
└── loop-engineering/   (core/python/loop_core/, deploy/python/loop_deploy/, contracts/python/loop_contracts/, controller/python/loop_controller/ (含 analyzer_rules.py), config/analyzer.yaml, connection/protocol/, connection/providers/adb/, scripts/le.sh, scripts/le_runs_cleanup.sh, templates/case-template.md, adapters/opencode/commands/le.md, pack.yaml)

profiles/android-system-enhance/
├── profile.yaml
├── workflows/ (lc-sync-code-to-patchs, lc-revert-code-from-patchs, lc-quick-fix-issue)
├── scripts/   (apply_preset_bugs.sh, mk_rpi5_full_image.sh)
├── config/    (baseline-status.yaml, baseline-evidence-template.yaml, target-paths.yaml, patch_knowledge_base.json)
├── reference/ (build-reference.md)
├── connection/providers/rp5-serial/
├── cases/     (7 yaml)
└── adapters/opencode/commands/ (lc-sync-code-to-patchs.md, lc-revert-code-from-patchs.md, lc-quick-fix-issue.md)

reference/     (lcharness-architecture.md, harness-optimization-blueprint.md, README.md)
docs/plans/    (3 lcharness plans)
docs/specs/    (1 lcharness spec)
README.md
.gitignore
```

---

## Task 1: LcHarness 基础骨架

**Files:**
- Create: `/mnt/d/Code/Github/LcHarness/.gitignore`
- Create: `/mnt/d/Code/Github/LcHarness/core/` (directory)
- Create: `/mnt/d/Code/Github/LcHarness/packs/` (directory)
- Create: `/mnt/d/Code/Github/LcHarness/profiles/` (directory)
- Create: `/mnt/d/Code/Github/LcHarness/reference/` (directory)
- Create: `/mnt/d/Code/Github/LcHarness/docs/plans/` (directory)
- Create: `/mnt/d/Code/Github/LcHarness/docs/specs/` (directory)

- [ ] **Step 1: 创建 .gitignore**

```bash
cat > /mnt/d/Code/Github/LcHarness/.gitignore <<'GITIGNORE'
# Python
__pycache__/
*.pyc
*.pyo

# 运行时产物
output/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db
GITIGNORE
```

- [ ] **Step 2: 创建所有目录**

```bash
mkdir -p /mnt/d/Code/Github/LcHarness/{core/{lib/{shell,python,bat},config,control-plane,rules,scripts},packs/{harness-validators,harness-tests,git-workflows,doc-governance,loop-engineering},profiles/android-system-enhance,reference,docs/{plans,specs}}
```

- [ ] **Step 3: 验证目录结构**

```bash
find /mnt/d/Code/Github/LcHarness -maxdepth 3 -type d | sort
```

Expected: 显示所有 core/packs/profiles/reference/docs 子目录。

- [ ] **Step 4: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat: 创建 LcHarness 基础目录骨架与 .gitignore"
```

---

## Task 2: 搬迁 core/ 目录

**Files:**
- Copy from: `engineering/harness/lib/` → `core/lib/`
- Copy from: `engineering/harness/config/harness-paths.conf` → `core/config/harness-paths.conf`
- Copy from: `engineering/harness/config/lcharness-layer-map.yaml` → `core/config/lcharness-layer-map.yaml`
- Copy from: `engineering/harness/control-plane/` → `core/control-plane/`
- Copy from: `engineering/harness/rules/` → `core/rules/`
- Copy from: `engineering/harness/scripts/check_access.sh` → `core/scripts/check_access.sh`

- [ ] **Step 1: 复制 lib/**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness"
DST="/mnt/d/Code/Github/LcHarness"

# shell + python (不含 __pycache__) + bat
cp "$SRC/lib/shell/"*.sh "$DST/core/lib/shell/"
cp "$SRC/lib/python/harness_path_util.py" "$DST/core/lib/python/"
cp "$SRC/lib/bat/harness_path_util.bat" "$DST/core/lib/bat/"
cp "$SRC/lib/README.md" "$DST/core/lib/README.md"

ls -la "$DST/core/lib/shell/" && ls "$DST/core/lib/python/" && ls "$DST/core/lib/bat/"
```

Expected: 文件拷贝成功，不含 `__pycache__`。

- [ ] **Step 2: 复制 config/（core 子集）**

```bash
cp "$SRC/config/harness-paths.conf" "$DST/core/config/harness-paths.conf"
cp "$SRC/config/lcharness-layer-map.yaml" "$DST/core/config/lcharness-layer-map.yaml"
```

- [ ] **Step 3: 复制 control-plane/（全部 8 文件）**

```bash
cp -r "$SRC/control-plane/"* "$DST/core/control-plane/"
ls "$DST/core/control-plane/"
```

Expected: 显示 lc-attach.sh, lc-detach.sh, lc-inject.sh, lc-reconcile.sh, lc-repo-registry.sh, lc-status.sh, lc-validate.sh, README.md。

- [ ] **Step 4: 复制 rules/（全部 9 文件）**

```bash
cp -r "$SRC/rules/"* "$DST/core/rules/"
ls "$DST/core/rules/"
```

Expected: 显示 manifest.yaml, README.md, cxx-coding-rules.md, doc-paths.md, parallel-strategy.md, path-management.md, plantuml.md, script-observability.md, source-code-modify.md。

- [ ] **Step 5: 复制 check_access.sh**

```bash
cp "$SRC/scripts/check_access.sh" "$DST/core/scripts/check_access.sh"
```

- [ ] **Step 6: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat(core): 搬迁运行时必须组件 lib/config/control-plane/rules/scripts"
```

---

## Task 3: 修正 core/ 中所有脚本的 source 路径

**关键变更**：旧结构中 control-plane/ 与 lib/shell/ 有两层深度差 (`../../lib/shell/`)，新结构中变为一层 (`../lib/shell/`)。同时 `harness_path_util.sh` 中硬编码的 paths.conf 路径需更新。

- [ ] **Step 1: 修正 control-plane/ 全部 7 脚本的 source 行**

```bash
DST="/mnt/d/Code/Github/LcHarness"

for f in "$DST/core/control-plane/"lc-*.sh; do
    sed -i 's|source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"|source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"|' "$f"
done

grep 'source.*harness_bootstrap' "$DST/core/control-plane/"lc-*.sh
```

Expected: 所有 7 个脚本显示 `source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"`。

- [ ] **Step 2: 修正 harness_path_util.sh 中 paths.conf 硬编码路径**

```bash
# 旧: $_H_PATH_REPO_ROOT/engineering/harness/config/harness-paths.conf
# 新: $_H_PATH_REPO_ROOT/core/config/harness-paths.conf
sed -i 's|engineering/harness/config/harness-paths.conf|core/config/harness-paths.conf|' "$DST/core/lib/shell/harness_path_util.sh"

grep 'paths.conf' "$DST/core/lib/shell/harness_path_util.sh"
```

Expected: 显示 `core/config/harness-paths.conf`。

- [ ] **Step 3: 验证 check_access.sh 的 source 路径（无需更改）**

check_access.sh 在旧结构中 `scripts/` → `../lib/shell/`，新结构中 `core/scripts/` → `../lib/shell/`，相对层级不变，但需确认：

```bash
grep 'source.*harness_bootstrap' "$DST/core/scripts/check_access.sh"
```

Expected: `source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"`（已正确）。

- [ ] **Step 4: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "fix(core): 修正 control-plane 脚本 source 路径和 paths.conf 硬编码引用"
```

---

## Task 4: 更新 harness-paths.conf

所有相对路径需移除 `engineering/` 前缀，因为 LcHarness 仓根就是原来的 `engineering/harness/` 层级。

- [ ] **Step 1: 重写 harness-paths.conf**

```bash
cat > /mnt/d/Code/Github/LcHarness/core/config/harness-paths.conf <<'CONF'
# ============================================================================
# harness-paths.conf — 统一路径配置（shell / python / bat 三方共用的单一事实源）
# 规则详见: core/rules/path-management.md (PATH-001)
#
# 格式约定:
#   - KEY="value"  简单 key=value（shell 可直接 source，python/bat 解析）
#   - 相对路径均相对于 REPO_ROOT 解析为绝对路径
#   - ENV_* 前缀: 环境可覆盖路径，实际值 = ${ENV_VAR:-default}
#   - 禁止在脚本中硬编码本文件已定义的路径
# ============================================================================

# --- 核心目录（LcHarness 仓内） ---
CORE_DIR="core"
LIBSHELL_DIR="core/lib/shell"
LIBPYTHON_DIR="core/lib/python"
LIBBAT_DIR="core/lib/bat"
CONTROL_PLANE_DIR="core/control-plane"
PACKS_DIR="packs"
PROFILES_DIR="profiles"

# --- 产物目录（位于 LcHarness 仓的 output/） ---
OUTPUT_DIR="output"
LOG_DIR="output/log"
HOST_LOG_DIR="output/host-log"
RUNS_DIR="output/runs"

# --- Python 包根（PYTHONPATH 用，冒号分隔） ---
PYTHON_PATH_ROOTS="packs/loop-engineering/core/python:packs/loop-engineering/contracts/python:packs/loop-engineering/controller/python:packs/loop-engineering/deploy/python"

# --- 环境可覆盖路径（实际值 = ${ENV_VAR:-default}） ---
# KERNEL_WS: kernel 源码工作区
ENV_KERNEL_WS="$HOME/workspace/rpi5-kernel-build/common"
# AOSP_WS: AOSP 源码工作区
ENV_AOSP_WS="$HOME/workspace/aosp"
# KERNEL_OUT: kernel 构建产物目录
ENV_KERNEL_OUT="$HOME/workspace/rpi5-kernel-build/out/android_rpi5"
# CLANG_BIN: kernel 构建用 clang 工具链 bin 目录
ENV_CLANG_BIN="$HOME/workspace/rpi5-kernel-build/prebuilts/clang/host/linux-x86/clang-r522817/bin"
# WINDOWS_IMG_DIR: 镜像输出目录（WSL 挂载 Windows 路径）
ENV_WINDOWS_IMG_DIR="/mnt/c/Files/RaspberryImages"
# LE_PATCH_GIT_ROOT: loop runtime 补丁隔离的 git 仓库根（vendor/lechao 本地 git）
ENV_LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/vendor/lechao"

# --- 测试沙箱 ---
TEST_SANDBOX_DIR="/tmp/opencode"

# --- 遗留兼容（待 Phase 4 全面替换） ---
HARNESS_DIR="."
ENGINEERING_DIR=""
LOOP_DIR="packs/loop-engineering"
LOOP_SCRIPTS_DIR="packs/loop-engineering/scripts"
LOOP_WORKFLOWS_DIR="packs/loop-engineering/workflows"
LOOP_CASES_DIR="packs/loop-engineering/cases"
SHELL_LIB_DIR="core/lib/shell"
PYTHON_LIB_DIR="core/lib/python"
BAT_LIB_DIR="core/lib/bat"
PATCHS_DIR=""   # 不在 LcHarness 仓内，由业务仓 paths.conf 覆盖
CONF
echo "harness-paths.conf written, $(wc -l < /mnt/d/Code/Github/LcHarness/core/config/harness-paths.conf) lines"
```

- [ ] **Step 2: 更新 lcharness-layer-map.yaml 中路径引用**

```bash
# 旧的路径前缀都带 engineering/harness/，新结构中需要全部替换
DST="/mnt/d/Code/Github/LcHarness"
sed -i 's|path: engineering/harness/config/|path: core/config/|' "$DST/core/config/lcharness-layer-map.yaml"
sed -i 's|path: engineering/harness/lib/|path: core/lib/|' "$DST/core/config/lcharness-layer-map.yaml"
sed -i 's|path: engineering/harness/rules/|path: core/rules/|' "$DST/core/config/lcharness-layer-map.yaml"
sed -i 's|path: engineering/harness/scripts/|path: core/scripts/|' "$DST/core/config/lcharness-layer-map.yaml"
sed -i 's|path: engineering/harness/control-plane/|path: core/control-plane/|' "$DST/core/config/lcharness-layer-map.yaml"
```

- [ ] **Step 3: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "fix(core): 更新 harness-paths.conf 为新仓路径结构，修正 layer-map 路径引用"
```

---

## Task 5: 搬迁 packs/harness-validators

**Files:**
- Copy from: `engineering/harness/scripts/validate_*.sh` x7 → `packs/harness-validators/`
- Copy from: `engineering/harness/scripts/run_all_validations.sh` → `packs/harness-validators/`
- Copy from: `engineering/harness/config/scope-mapping.yaml` → `packs/harness-validators/`
- Create: `packs/harness-validators/pack.yaml`

- [ ] **Step 1: 复制校验器脚本和配置文件**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness"
DST="/mnt/d/Code/Github/LcHarness"

cp "$SRC/scripts/validate_baseline_status.sh" "$DST/packs/harness-validators/"
cp "$SRC/scripts/validate_harness_config.sh" "$DST/packs/harness-validators/"
cp "$SRC/scripts/validate_harness_docs.sh" "$DST/packs/harness-validators/"
cp "$SRC/scripts/validate_harness_scripts.sh" "$DST/packs/harness-validators/"
cp "$SRC/scripts/validate_lcharness_layer_map.sh" "$DST/packs/harness-validators/"
cp "$SRC/scripts/validate_manifest.sh" "$DST/packs/harness-validators/"
cp "$SRC/scripts/validate_workflow_contracts.sh" "$DST/packs/harness-validators/"
cp "$SRC/scripts/run_all_validations.sh" "$DST/packs/harness-validators/"
cp "$SRC/config/scope-mapping.yaml" "$DST/packs/harness-validators/"

ls "$DST/packs/harness-validators/"
```

Expected: 9 个文件。

- [ ] **Step 2: 修正所有脚本的 source 路径**

旧路径 `../lib/shell/`，新路径 `../../core/lib/shell/`：

```bash
for f in "$DST/packs/harness-validators/"*.sh; do
    sed -i 's|source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"|source "$SCRIPT_DIR/../../core/lib/shell/harness_bootstrap.sh"|' "$f"
done

grep 'source.*harness_bootstrap' "$DST/packs/harness-validators/"*.sh | head -5
```

Expected: 所有脚本显示 `source "$SCRIPT_DIR/../../core/lib/shell/harness_bootstrap.sh"`。

- [ ] **Step 3: 创建 pack.yaml**

```bash
cat > /mnt/d/Code/Github/LcHarness/packs/harness-validators/pack.yaml <<'YAML'
version: 1
name: harness-validators
type: platform
description: LcHarness 全量校验器集，包括 config/docs/scripts/manifest/workflow/layer-map 各项校验
internal: true
YAML
```

- [ ] **Step 4: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat(packs): 搬迁 harness-validators（通用校验器 pack）"
```

---

## Task 6: 搬迁 packs/harness-tests

**Files:**
- Copy from: `engineering/harness/tests/` (all) → `packs/harness-tests/`
- Create: `packs/harness-tests/pack.yaml`

- [ ] **Step 1: 复制测试文件（不含 __pycache__）**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness"
DST="/mnt/d/Code/Github/LcHarness"

cp -r "$SRC/tests/"* "$DST/packs/harness-tests/"

# 移除 __pycache__（如果有）
rm -rf "$DST/packs/harness-tests/"__pycache__ 2>/dev/null

ls "$DST/packs/harness-tests/"*.sh | wc -l
```

Expected: 12 个 `.sh` 文件 + fixtures/ + README.md。

- [ ] **Step 2: 修正所有测试脚本的 source 路径**

```bash
for f in "$DST/packs/harness-tests/"*.sh; do
    sed -i 's|source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"|source "$SCRIPT_DIR/../../core/lib/shell/harness_bootstrap.sh"|' "$f"
done

# 特殊处理 test_le_runs_cleanup.sh（引用 loop 路径的）
# 该脚本引用的 loop 路径也需更新
sed -i 's|engineering/loop/|packs/loop-engineering/|g' "$DST/packs/harness-tests/test_le_runs_cleanup.sh" 2>/dev/null || true

grep 'source.*harness_bootstrap' "$DST/packs/harness-tests/"*.sh | head -3
```

Expected: 所有脚本显示 `source "$SCRIPT_DIR/../../core/lib/shell/harness_bootstrap.sh"`。

- [ ] **Step 3: 创建 pack.yaml**

```bash
cat > /mnt/d/Code/Github/LcHarness/packs/harness-tests/pack.yaml <<'YAML'
version: 1
name: harness-tests
type: platform
description: LcHarness 全量测试框架与 fixtures，覆盖所有 pack 和 core 的回归测试
internal: true
YAML
```

- [ ] **Step 4: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat(packs): 搬迁 harness-tests（通用测试 pack）"
```

---

## Task 7: 搬迁 packs/git-workflows

**Files:**
- Copy from: `engineering/harness/workflows/lc-git-push-to-server/` → `packs/git-workflows/workflows/`
- Copy from: `.opencode/commands/lc-git-push-to-server.md` → `packs/git-workflows/adapters/opencode/commands/`
- Create: `packs/git-workflows/pack.yaml`

- [ ] **Step 1: 复制 workflow 文件**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness"
DST="/mnt/d/Code/Github/LcHarness"

mkdir -p "$DST/packs/git-workflows/workflows/lc-git-push-to-server"
cp -r "$SRC/workflows/lc-git-push-to-server/"* "$DST/packs/git-workflows/workflows/lc-git-push-to-server/"

ls "$DST/packs/git-workflows/workflows/lc-git-push-to-server/"
```

Expected: commit_and_push.sh, collect_diff.sh, WORKFLOW.md, README.md。

- [ ] **Step 2: 修正 workflow 脚本的 source 路径**

旧路径 `../../lib/shell/`，新路径 `../../../../core/lib/shell/`：

```bash
for f in "$DST/packs/git-workflows/workflows/lc-git-push-to-server/"*.sh; do
    sed -i 's|source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"|source "$SCRIPT_DIR/../../../../core/lib/shell/harness_bootstrap.sh"|' "$f"
done

grep 'source.*harness_bootstrap' "$DST/packs/git-workflows/workflows/lc-git-push-to-server/"*.sh
```

Expected: `source "$SCRIPT_DIR/../../../../core/lib/shell/harness_bootstrap.sh"`。

- [ ] **Step 3: 复制 adapter 文件**

```bash
mkdir -p "$DST/packs/git-workflows/adapters/opencode/commands"
cp "/mnt/d/Code/Github/AndroidSystemEnhance/.opencode/commands/lc-git-push-to-server.md" "$DST/packs/git-workflows/adapters/opencode/commands/lc-git-push-to-server.md"
```

- [ ] **Step 4: 创建 pack.yaml**

```bash
cat > /mnt/d/Code/Github/LcHarness/packs/git-workflows/pack.yaml <<'YAML'
version: 1
name: git-workflows
type: platform
description: Git 服务端推送 workflow，通用 git 项目可复用
capabilities:
  skills:
    - name: lc-git-push-to-server
      entry: workflows/lc-git-push-to-server
YAML
```

- [ ] **Step 5: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat(packs): 搬迁 git-workflows pack"
```

---

## Task 8: 搬迁 packs/doc-governance

**Files:**
- Copy from: `engineering/harness/workflows/lc-sync-patchs-to-doc/` → `packs/doc-governance/workflows/`
- Copy from: `engineering/harness/templates/` → `packs/doc-governance/templates/`
- Copy from: `engineering/harness/config/doc-sync-mapping.yaml` → `packs/doc-governance/`
- Copy from: `.opencode/commands/lc-sync-patchs-to-doc.md` → `packs/doc-governance/adapters/opencode/commands/`
- Create: `packs/doc-governance/pack.yaml`

- [ ] **Step 1: 复制文件**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness"
DST="/mnt/d/Code/Github/LcHarness"

mkdir -p "$DST/packs/doc-governance/workflows/lc-sync-patchs-to-doc"
cp -r "$SRC/workflows/lc-sync-patchs-to-doc/"* "$DST/packs/doc-governance/workflows/lc-sync-patchs-to-doc/"
cp -r "$SRC/templates/"* "$DST/packs/doc-governance/templates/"
cp "$SRC/config/doc-sync-mapping.yaml" "$DST/packs/doc-governance/doc-sync-mapping.yaml"
mkdir -p "$DST/packs/doc-governance/adapters/opencode/commands"
cp "/mnt/d/Code/Github/AndroidSystemEnhance/.opencode/commands/lc-sync-patchs-to-doc.md" "$DST/packs/doc-governance/adapters/opencode/commands/lc-sync-patchs-to-doc.md"
```

- [ ] **Step 2: 修正 workflow 脚本的 source 路径**

```bash
for f in "$DST/packs/doc-governance/workflows/lc-sync-patchs-to-doc/"*.sh; do
    sed -i 's|source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"|source "$SCRIPT_DIR/../../../../core/lib/shell/harness_bootstrap.sh"|' "$f"
done

grep 'source.*harness_bootstrap' "$DST/packs/doc-governance/workflows/lc-sync-patchs-to-doc/"*.sh
```

- [ ] **Step 3: 创建 pack.yaml**

```bash
cat > /mnt/d/Code/Github/LcHarness/packs/doc-governance/pack.yaml <<'YAML'
version: 1
name: doc-governance
type: platform
description: 文档治理通用 pack，包括 patch-to-doc 同步、模板管理
capabilities:
  skills:
    - name: lc-sync-patchs-to-doc
      entry: workflows/lc-sync-patchs-to-doc
YAML
```

- [ ] **Step 4: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat(packs): 搬迁 doc-governance pack"
```

---

## Task 9: 搬迁 packs/loop-engineering

**Files:**
- Copy from `engineering/loop/` → `packs/loop-engineering/`（选择性搬迁，不含 __pycache__）
- Copy from `.opencode/commands/le.md` → `packs/loop-engineering/adapters/opencode/commands/`
- Create: `packs/loop-engineering/pack.yaml`
- Rename: `lcview_analyzer_rules.py` → `analyzer_rules.py`

- [ ] **Step 1: 复制 loop/ 核心文件**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/loop"
DST="/mnt/d/Code/Github/LcHarness/packs/loop-engineering"

# controller, contracts, deploy 代码
cp -r "$SRC/core/python/loop_core" "$DST/core/python/loop_core"
cp -r "$SRC/deploy/python/loop_deploy" "$DST/deploy/python/loop_deploy"
cp -r "$SRC/contracts/python/loop_contracts" "$DST/contracts/python/loop_contracts"
cp -r "$SRC/controller/python/loop_controller" "$DST/controller/python/loop_controller"

# 配置文件
cp "$SRC/config/analyzer.yaml" "$DST/config/analyzer.yaml"

# connection 通用部分
mkdir -p "$DST/connection/protocol"
cp -r "$SRC/connection/protocol/"* "$DST/connection/protocol/"
mkdir -p "$DST/connection/providers/adb"
cp -r "$SRC/connection/providers/adb/"* "$DST/connection/providers/adb/"

# 入口脚本
mkdir -p "$DST/scripts"
cp "$SRC/scripts/le.sh" "$DST/scripts/le.sh"
cp "$SRC/scripts/le_runs_cleanup.sh" "$DST/scripts/le_runs_cleanup.sh"

# 模板
mkdir -p "$DST/templates"
cp "$SRC/templates/case-template.md" "$DST/templates/case-template.md"

# README / WORKFLOW
cp "$SRC/README.md" "$DST/README.md" 2>/dev/null || true
cp "$SRC/WORKFLOW.md" "$DST/WORKFLOW.md" 2>/dev/null || true

# adapter
mkdir -p "$DST/adapters/opencode/commands"
cp "/mnt/d/Code/Github/AndroidSystemEnhance/.opencode/commands/le.md" "$DST/adapters/opencode/commands/le.md"

# 清理所有 __pycache__
find "$DST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

echo "Loop files copied. Subdirs:"
find "$DST" -maxdepth 2 -type d | sort
```

- [ ] **Step 2: 重命名 lcview_analyzer_rules → analyzer_rules**

```bash
DST="/mnt/d/Code/Github/LcHarness/packs/loop-engineering"

# 1. 重命名文件
if [ -f "$DST/controller/python/loop_controller/lcview_analyzer_rules.py" ]; then
    mv "$DST/controller/python/loop_controller/lcview_analyzer_rules.py" "$DST/controller/python/loop_controller/analyzer_rules.py"
    echo "Renamed: lcview_analyzer_rules.py -> analyzer_rules.py"
fi

# 2. 查找所有引用并替换 import 和类名
grep -rn 'lcview_analyzer_rules' "$DST" --include='*.py' -l
```

Expected: 列出所有引用了 `lcview_analyzer_rules` 的 Python 文件。

```bash
# 3. 批量替换所有引用
find "$DST" -name '*.py' -exec sed -i 's/lcview_analyzer_rules/analyzer_rules/g' {} +

# 4. 验证替换完成
grep -rn 'lcview_analyzer_rules' "$DST" --include='*.py' | wc -l
```

Expected: `0`（无残留引用）。

- [ ] **Step 3: 修正 loop 脚本的 source 路径**

`le.sh` 和 `le_runs_cleanup.sh` 可能引用了 `engineering/` 路径或 source 了 harness 库：

```bash
DST="/mnt/d/Code/Github/LcHarness/packs/loop-engineering"

# 检查所有 shell 脚本的 source/路径引用
grep -rn 'engineering/' "$DST" --include='*.sh' -l 2>/dev/null
grep -rn 'harness_bootstrap\|harness_path' "$DST" --include='*.sh' -l 2>/dev/null
```

Expected: 如果有引用 engineering/，需要手动修正为相对于 LcHarness 的路径。

```bash
# 修正常见的 engineering/ 路径引用
find "$DST" -name '*.sh' -exec sed -i 's|engineering/harness/lib|core/lib|g' {} +
find "$DST" -name '*.sh' -exec sed -i 's|engineering/harness/config|core/config|g' {} +
find "$DST" -name '*.sh' -exec sed -i 's|engineering/loop|packs/loop-engineering|g' {} +
find "$DST" -name '*.sh' -exec sed -i 's|engineering/harness/control-plane|core/control-plane|g' {} +
```

- [ ] **Step 4: 创建 pack.yaml**

```bash
cat > /mnt/d/Code/Github/LcHarness/packs/loop-engineering/pack.yaml <<'YAML'
version: 1
name: loop-engineering
type: solution
description: AI 驱动设备验收闭环框架 — 通用测试引擎 + 部署 + 分析器 + 断言
entry_scripts:
  - name: le
    path: scripts/le.sh
    adapter_type: opencode
YAML
```

- [ ] **Step 5: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat(packs): 搬迁 loop-engineering pack，重命名 lcview_analyzer_rules -> analyzer_rules"
```

---

## Task 10: 搬迁 profiles/android-system-enhance

**Files:**
- Copy from `engineering/harness/workflows/` (ASE 专属 3 个) → `profiles/android-system-enhance/workflows/`
- Copy from `engineering/harness/scripts/apply_preset_bugs.sh` → `profiles/android-system-enhance/scripts/`
- Copy from `engineering/harness/scripts/mk_rpi5_full_image.sh` → `profiles/android-system-enhance/scripts/`
- Copy from `engineering/harness/config/baseline-status.yaml` → `profiles/android-system-enhance/config/`
- Copy from `engineering/harness/config/baseline-evidence-template.yaml` → `profiles/android-system-enhance/config/`
- Copy from `engineering/harness/reference/build-reference.md` → `profiles/android-system-enhance/reference/`
- Copy from `engineering/loop/config/target-paths.yaml` → `profiles/android-system-enhance/config/`
- Copy from `engineering/loop/config/patch_knowledge_base.json` → `profiles/android-system-enhance/config/`
- Copy from `engineering/loop/cases/` → `profiles/android-system-enhance/cases/`
- Copy from `engineering/loop/connection/providers/rp5-serial/` → `profiles/android-system-enhance/connection/providers/rp5-serial/`
- Copy from `.opencode/commands/`  (ASE 专属 3 个) → `profiles/android-system-enhance/adapters/opencode/commands/`
- Create: `profiles/android-system-enhance/profile.yaml`

- [ ] **Step 1: 复制 ASE 专属 workflows**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness"
DST="/mnt/d/Code/Github/LcHarness/profiles/android-system-enhance"

mkdir -p "$DST/workflows"

cp -r "$SRC/workflows/lc-sync-code-to-patchs" "$DST/workflows/"
cp -r "$SRC/workflows/lc-revert-code-from-patchs" "$DST/workflows/"
cp -r "$SRC/workflows/lc-quick-fix-issue" "$DST/workflows/"

ls -d "$DST/workflows/"*/
```

Expected: 3 个 workflow 目录。

- [ ] **Step 2: 修正 workflow 脚本的 source 路径**

```bash
for d in "$DST/workflows/"*/; do
    for f in "$d"*.sh; do
        [ -f "$f" ] && sed -i 's|source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"|source "$SCRIPT_DIR/../../../../core/lib/shell/harness_bootstrap.sh"|' "$f"
        [ -f "$f" ] && sed -i 's|engineering/harness/|core/|g' "$f"
        [ -f "$f" ] && sed -i 's|engineering/loop/|packs/loop-engineering/|g' "$f"
    done
done

grep -rn 'source.*harness_bootstrap' "$DST/workflows/"
```

Expected: 所有脚本 source 路径已更新。

- [ ] **Step 3: 复制 ASE 专属脚本**

```bash
mkdir -p "$DST/scripts"
cp "$SRC/scripts/apply_preset_bugs.sh" "$DST/scripts/apply_preset_bugs.sh"
cp "$SRC/scripts/mk_rpi5_full_image.sh" "$DST/scripts/mk_rpi5_full_image.sh"

# 修正 source 路径
for f in "$DST/scripts/"*.sh; do
    sed -i 's|source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"|source "$SCRIPT_DIR/../../../core/lib/shell/harness_bootstrap.sh"|' "$f"
    sed -i 's|engineering/harness/|core/|g' "$f"
    sed -i 's|engineering/loop/|packs/loop-engineering/|g' "$f"
done
```

- [ ] **Step 4: 复制配置文件**

```bash
mkdir -p "$DST/config"
cp "$SRC/config/baseline-status.yaml" "$DST/config/baseline-status.yaml"
cp "$SRC/config/baseline-evidence-template.yaml" "$DST/config/baseline-evidence-template.yaml"

LOOP_SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/loop"
cp "$LOOP_SRC/config/target-paths.yaml" "$DST/config/target-paths.yaml"
cp "$LOOP_SRC/config/patch_knowledge_base.json" "$DST/config/patch_knowledge_base.json"
```

- [ ] **Step 5: 复制 reference**

```bash
mkdir -p "$DST/reference"
cp "$SRC/reference/build-reference.md" "$DST/reference/build-reference.md"
```

- [ ] **Step 6: 复制 ASE 专属连接和测试用例**

```bash
mkdir -p "$DST/connection/providers"
cp -r "$LOOP_SRC/connection/providers/rp5-serial" "$DST/connection/providers/rp5-serial"

mkdir -p "$DST/cases"
cp -r "$LOOP_SRC/cases/"* "$DST/cases/"

# 清理 __pycache__
find "$DST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
```

- [ ] **Step 7: 复制 adapters**

```bash
mkdir -p "$DST/adapters/opencode/commands"
OPENCODE="/mnt/d/Code/Github/AndroidSystemEnhance/.opencode/commands"
cp "$OPENCODE/lc-sync-code-to-patchs.md" "$DST/adapters/opencode/commands/lc-sync-code-to-patchs.md"
cp "$OPENCODE/lc-revert-code-from-patchs.md" "$DST/adapters/opencode/commands/lc-revert-code-from-patchs.md"
cp "$OPENCODE/lc-quick-fix-issue.md" "$DST/adapters/opencode/commands/lc-quick-fix-issue.md"
```

- [ ] **Step 8: 创建 profile.yaml**

```bash
cat > /mnt/d/Code/Github/LcHarness/profiles/android-system-enhance/profile.yaml <<'YAML'
version: 1
name: android-system-enhance
description: AndroidSystemEnhance 项目首个 profile，装配 Android/AOSP 领域专属能力
packs:
  - git-workflows
  - doc-governance
  - loop-engineering

projection:
  skills:
    - le
    - lc-sync-code-to-patchs
    - lc-revert-code-from-patchs
    - lc-quick-fix-issue
    - lc-git-push-to-server
    - lc-sync-patchs-to-doc

  workflows:
    - name: lc-sync-code-to-patchs
      entry: profiles/android-system-enhance/workflows/lc-sync-code-to-patchs/sync_code_to_patchs.sh
    - name: lc-revert-code-from-patchs
      entry: profiles/android-system-enhance/workflows/lc-revert-code-from-patchs/revert_code_from_patchs.sh
    - name: lc-quick-fix-issue
      entry: profiles/android-system-enhance/workflows/lc-quick-fix-issue/detect_test_env.sh
    - name: lc-git-push-to-server
      entry: packs/git-workflows/workflows/lc-git-push-to-server/commit_and_push.sh
    - name: lc-sync-patchs-to-doc
      entry: packs/doc-governance/workflows/lc-sync-patchs-to-doc/sync_patchs_to_doc.sh
    - name: le
      entry: packs/loop-engineering/scripts/le.sh

  runtime:
    - bash
    - python3

target:
  repo: android-system-enhance
  workspace_root: /
  overlay_dir: .lcharness
YAML
```

- [ ] **Step 9: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "feat(profiles): 创建 android-system-enhance profile（ASE 专属能力 + loop cases + 配置）"
```

---

## Task 11: 搬迁 reference/ 和 docs/

**Files:**
- Copy from: `engineering/harness/reference/lcharness-architecture.md` → `reference/lcharness-architecture.md`
- Copy from: `engineering/harness/reference/harness-optimization-blueprint.md` → `reference/harness-optimization-blueprint.md`
- Copy from: `engineering/harness/reference/README.md` → `reference/README.md`
- Copy from: `docs/plans/2026-07-02-lcharness-*.md` x3 → `docs/plans/`
- Copy from: `docs/specs/2026-07-02-lcharness-framework-design.md` → `docs/specs/`

- [ ] **Step 1: 复制 reference**

```bash
SRC="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness"
DST="/mnt/d/Code/Github/LcHarness"

cp "$SRC/reference/lcharness-architecture.md" "$DST/reference/lcharness-architecture.md"
cp "$SRC/reference/harness-optimization-blueprint.md" "$DST/reference/harness-optimization-blueprint.md"
cp "$SRC/reference/README.md" "$DST/reference/README.md"

# 修正 reference 内部的相对链接路径（如有）
sed -i 's|engineering/harness/|core/|g' "$DST/reference/lcharness-architecture.md"
```

- [ ] **Step 2: 复制设计文档**

```bash
ASRC="/mnt/d/Code/Github/AndroidSystemEnhance"
cp "$ASRC/docs/plans/2026-07-02-lcharness-phase1-architecture-refactor-plan.md" "$DST/docs/plans/"
cp "$ASRC/docs/plans/2026-07-02-lcharness-phase2-control-plane-plan.md" "$DST/docs/plans/"
cp "$ASRC/docs/plans/2026-07-03-lcharness-phase3-migration.md" "$DST/docs/plans/"
cp "$ASRC/docs/specs/2026-07-02-lcharness-framework-design.md" "$DST/docs/specs/"
```

- [ ] **Step 3: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "docs: 搬迁架构参考文档、设计 spec 和实施 plans"
```

---

## Task 12: 创建 LcHarness 根 README.md

- [ ] **Step 1: 编写根 README.md**

基于旧 `engineering/harness/README.md` 重写，适配新仓结构：

```bash
cat > /mnt/d/Code/Github/LcHarness/README.md <<'README'
# LcHarness — 本地注入式通用工程能力框架

LcHarness 是一个个人独占使用的独立工程能力仓，通过本地 overlay 注入方式为多个业务仓提供工程增强能力（校验、工作流、测试框架、AI 驱动验收闭环），同时保持业务仓零 tracked 污染。

## 架构

```
core/        运行时必须组件（lib、config、control-plane、rules）
packs/       通用非必须能力包（validators、tests、git-workflows、doc-governance、loop-engineering）
profiles/    面向特定业务仓的装配层（首个: android-system-enhance）
reference/   架构参考与蓝图文档
```

**依赖方向**: `profiles → packs → core`，无反向依赖。

## 快速命令

全局别名（写入 `~/.bashrc` 或 `AGENTS.md`）：

```bash
alias lc-attach='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-attach.sh'
alias lc-status='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-status.sh'
alias lc-detach='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-detach.sh'
alias lc-validate='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-validate.sh'
alias lc-reconcile='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-reconcile.sh'
```

## 控制面脚本

| 命令 | 说明 | 用法 |
|------|------|------|
| `lc-attach` | 附加 repo 到 LcHarness | `lc-attach <repo-path> --profile <name>` |
| `lc-inject` | 注入 overlay | `lc-inject <repo-id>` |
| `lc-status` | 查看 repo 状态 | `lc-status <repo-id>` |
| `lc-validate` | 验证 overlay 健康 | `lc-validate <repo-id>` |
| `lc-reconcile` | 修复非健康状态 | `lc-reconcile <repo-id>` |
| `lc-detach` | 分离 repo | `lc-detach <repo-id>` |

详见 [core/control-plane/README.md](core/control-plane/README.md)。

## Packs

| Pack | 类型 | 说明 |
|------|------|------|
| `harness-validators` | platform | 全量校验器集 |
| `harness-tests` | platform | 测试框架与 fixtures |
| `git-workflows` | platform | Git 推送 workflow |
| `doc-governance` | platform | 文档治理（同步、模板） |
| `loop-engineering` | solution | AI 驱动设备验收闭环框架 |

## Profiles

| Profile | 目标仓 | 启用 Packs |
|---------|--------|-----------|
| [`android-system-enhance`](profiles/android-system-enhance/profile.yaml) | AndroidSystemEnhance | git-workflows, doc-governance, loop-engineering |

## 投影模型

- **内部能力**（core + harness-validators + harness-tests）：仅 LcHarness 内部使用，不投影到业务仓
- **可投影能力**（packs + profiles）：由 profile.yaml 声明，通过注入引擎投影到业务仓 `.lcharness/` overlay 目录
- 业务仓 overlay 采用 symlink/映射优先模型，零 tracked 污染

## 相关文档

| 文档 | 路径 |
|------|------|
| 总体设计 | [docs/specs/2026-07-02-lcharness-framework-design.md](docs/specs/2026-07-02-lcharness-framework-design.md) |
| 架构参考 | [reference/lcharness-architecture.md](reference/lcharness-architecture.md) |
| Phase 1 计划 | [docs/plans/2026-07-02-lcharness-phase1-architecture-refactor-plan.md](docs/plans/2026-07-02-lcharness-phase1-architecture-refactor-plan.md) |
| Phase 2 计划 | [docs/plans/2026-07-02-lcharness-phase2-control-plane-plan.md](docs/plans/2026-07-02-lcharness-phase2-control-plane-plan.md) |
| Phase 3 计划 | [docs/plans/2026-07-03-lcharness-phase3-migration.md](docs/plans/2026-07-03-lcharness-phase3-migration.md) |
README
echo "README.md written"
```

- [ ] **Step 2: Commit to LcHarness**

```bash
cd /mnt/d/Code/Github/LcHarness && git add -A && git commit -m "docs: 创建 LcHarness 根 README.md"
```

---

## Task 13: 验证与修复

- [ ] **Step 1: 验证所有 scripts 的 source 路径**

```bash
cd /mnt/d/Code/Github/LcHarness

# 列出所有 .sh 脚本及其 source 路径
echo "=== All scripts with harness bootstrap source ==="
grep -rn 'source.*harness_bootstrap\|source.*harness_path' --include='*.sh' . | grep -v '.git/' | sort

echo ""
echo "=== Checking for any remaining 'engineering/' hardcodes ==="
grep -rn 'engineering/' --include='*.sh' --include='*.py' . | grep -v '.git/' | grep -v 'docs/' | head -20
```

Expected:
1. 所有 `harness_bootstrap` source 路径指向 `core/lib/shell/`（相对深度因脚本位置不同而异）
2. `engineering/` 硬编码引用应为 0（或仅在注释中出现）

- [ ] **Step 2: 验证目录完整性**

```bash
cd /mnt/d/Code/Github/LcHarness

echo "=== Expected top-level dirs ==="
for d in core packs profiles reference docs; do
    [ -d "$d" ] && echo "  $d/ ✓" || echo "  $d/ ✗ MISSING"
done

echo ""
echo "=== Expected subdirs under core/ ==="
for d in core/lib core/lib/shell core/lib/python core/lib/bat core/config core/control-plane core/rules core/scripts; do
    [ -d "$d" ] && echo "  $d/ ✓" || echo "  $d/ ✗ MISSING"
done

echo ""
echo "=== Expected packs ==="
for d in packs/harness-validators packs/harness-tests packs/git-workflows packs/doc-governance packs/loop-engineering; do
    [ -d "$d" ] && echo "  $d/ ✓" || echo "  $d/ ✗ MISSING"
done

echo ""
echo "=== File counts ==="
echo "core scripts: $(find core -name '*.sh' | wc -l)"
echo "pack scripts: $(find packs -name '*.sh' | wc -l)"
echo "profile scripts: $(find profiles -name '*.sh' | wc -l)"
echo "pack yamls: $(find packs -name 'pack.yaml' | wc -l)"
echo "profile yamls: $(find profiles -name 'profile.yaml' | wc -l)"
```

Expected: 所有目录 ✓，file counts 匹配。

- [ ] **Step 3: 验证 lcview_analyzer_rules 重命名完成**

```bash
cd /mnt/d/Code/Github/LcHarness
grep -rn 'lcview_analyzer_rules' --include='*.py' . | grep -v '.git/' | wc -l
```

Expected: `0`。

- [ ] **Step 4: 验证 no __pycache__**

```bash
cd /mnt/d/Code/Github/LcHarness
find . -type d -name "__pycache__" | grep -v '.git/' | wc -l
```

Expected: `0`。

- [ ] **Step 5: Commit to LcHarness（如有修复）**

```bash
cd /mnt/d/Code/Github/LcHarness
if ! git diff --quiet; then
    git add -A && git commit -m "fix: 验证后修复残留问题"
fi
```

---

## Task 14: 清理 AndroidSystemEnhance

**重要**：根据 AGENTS.md 文件删除规则，所有删除操作需逐项向用户确认后再执行。

待删除的目录/文件清单（用户需确认）：

| # | 路径 | 类型 | 说明 |
|---|------|------|------|
| 1 | `engineering/harness/` | 目录 | 完全搬迁到 LcHarness |
| 2 | `engineering/loop/` | 目录 | 完全搬迁到 LcHarness |
| 3 | `engineering/README.md` | 文件 | 已迁移到 LcHarness README |
| 4 | `engineering/output/` | 目录 | 运行时产物，检查 `git check-ignore` 后决定 |
| 5 | `.opencode/commands/le.md` | 文件 | 已搬迁到 LcHarness adapters |
| 6 | `.opencode/commands/lc-sync-code-to-patchs.md` | 文件 | 已搬迁到 LcHarness |
| 7 | `.opencode/commands/lc-revert-code-from-patchs.md` | 文件 | 已搬迁到 LcHarness |
| 8 | `.opencode/commands/lc-git-push-to-server.md` | 文件 | 已搬迁到 LcHarness |
| 9 | `.opencode/commands/lc-quick-fix-issue.md` | 文件 | 已搬迁到 LcHarness |
| 10 | `.opencode/commands/lc-sync-patchs-to-doc.md` | 文件 | 已搬迁到 LcHarness |

**待修改文件**（不删除，仅改内容）：

| # | 路径 | 修改操作 |
|---|------|---------|
| 11 | `AGENTS.md` | 更新 LcHarness 别名路径为 `/mnt/d/Code/Github/LcHarness/core/control-plane/`；更新规则引用路径 |

- [ ] **Step 1: 向用户确认删除清单**

向用户展示以上 10 项待删清单 + 1 项待改清单，等待用户逐项确认（`y` / 同意 / 删吧）。

- [ ] **Step 2: 执行确认后的删除**

用户确认后：
```bash
cd /mnt/d/Code/Github/AndroidSystemEnhance

# 按用户确认的清单逐项删除
# （不在此 plan 中预设具体删除命令，等用户确认后执行）
```

- [ ] **Step 3: 更新 AGENTS.md**

将第 67-75 行的 LcHarness 别名替换为新路径：

```bash
# 旧别名块
OLD_ALIAS_BLOCK='## LcHarness 控制面快捷命令\n\n```bash\nalias lc-attach.*\nalias lc-status.*\nalias lc-detach.*\nalias lc-validate.*\nalias lc-reconcile.*\n```'

# 替换为新路径
cat > /tmp/ag_new_aliases.md <<'ALIASES'
## LcHarness 控制面快捷命令

```bash
alias lc-attach='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-attach.sh'
alias lc-status='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-status.sh'
alias lc-detach='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-detach.sh'
alias lc-validate='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-validate.sh'
alias lc-reconcile='bash /mnt/d/Code/Github/LcHarness/core/control-plane/lc-reconcile.sh'
```
ALIASES
```

同时更新 AGENTS.md 中所有 `engineering/harness/rules/` 引用为 `/mnt/d/Code/Github/LcHarness/core/rules/`（保留相对路径，确保在 ASE 中打开文档时链接依然有效）。

- [ ] **Step 4: Commit to AndroidSystemEnhance**

```bash
cd /mnt/d/Code/Github/AndroidSystemEnhance && git add -A && git commit -m "refactor: 迁移 engineering/harness + loop 到独立 LcHarness 仓"
```

---

## Task 15: 最终验证

- [ ] **Step 1: 确认 LcHarness git log**

```bash
cd /mnt/d/Code/Github/LcHarness && git log --oneline
```

Expected: 10+ commits，覆盖全部搬迁步骤。

- [ ] **Step 2: 确认 ASE git log**

```bash
cd /mnt/d/Code/Github/AndroidSystemEnhance && git log --oneline -3
```

- [ ] **Step 3: 快速 smoke test**

```bash
# 在新的 LcHarness 仓运行基本路径解析测试
cd /mnt/d/Code/Github/LcHarness

# 测试 harness_path 是否可正常解析
bash -c '
  source core/lib/shell/harness_path_util.sh
  echo "REPO_ROOT: $(harness_repo_root)"
  echo "CORE_DIR: $(harness_path CORE_DIR)"
  echo "CONTROL_PLANE_DIR: $(harness_path CONTROL_PLANE_DIR)"
'
```

Expected: 输出正确的 LcHarness 仓根路径和子目录路径。

- [ ] **Step 4: 运行所有 commit**

已完成，每次 Task 独立 commit。

---

## 投影引擎设计附录

Phase 3 仅定义投影引擎的**规格**（函数级接口），不实现调用。实现留给 Phase 4。

### 核心函数签名（不实现）

```
# 投影引擎设计规格 — Phase 4 实现
#
# resolve_profile(profile_path) → {
#   packs: [str],           # 从 profile.yaml 解析的 packs 列表
#   skills: [str],          # 白名单 skills
#   workflows: [{name, entry}], # 白名单 workflows
#   runtime: [str]          # 允许的运行时
# }
#
# collect_capabilities(profile) → {
#   adapters: [{name, source_path, target_path}],
#   workflows: [{name, source_path, target_path}],
# }
#   # 来源：profile 自身的 adapters/ + 各 pack 的 adapters/
#   # 过滤：仅保留 profile.yaml 白名单中的 skills/workflows
#
# project_overlay(repo_id, capabilities) → overlay_root
#   # 在业务仓 .lcharness/ 下创建 capabilities/ 目录
#   # 对每个 capability 创建 symlink（source → target）
#   # 写入 capability manifest（白名单副本）
```
```

---

## Notes for the executor

1. 所有 `cp` 命令排除 `__pycache__/` 目录
2. `source` 路径修正参考各组脚本的旧路径模式批量替换
3. LcHarness 首次 commit 建议包含 .gitignore
4. 搬迁完成后的 ASE 残留需用户逐项确认删除
5. 投影引擎本阶段仅定规格，不编码
6. `engineering/output/` 和 `engineering/harness/tmp/` 不搬迁，但删除前需用户确认

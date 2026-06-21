# Loop Engineering 边界收敛与控制面重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛 `engineering/` 顶层边界，严格把 loop-specific 组件从 `engineering/harness/` 回迁到 `engineering/loop/`，并在 `engineering/loop/` 下建立 `scripts/`、`controller/`、`workflows/`、`contracts/` 四类控制面骨架，为后续 1-7 自动化闭环提供最小可演进结构。

**Architecture:** 保持 `engineering/harness/` 作为公共工程基础设施层，`engineering/loop/` 作为 loop engineering 专属能力层，并固定依赖方向为 `loop -> harness`。实现顺序遵循“先立边界文档，再迁移误归属组件，再补 contracts/controller/workflows 最小骨架，最后统一回归验证”，避免在目录边界未收敛前继续累积 loop-specific 污染。

**Tech Stack:** Markdown 文档、bash / bat 脚本、Python 3 dataclass + pytest、现有 harness bootstrap/path/observability 公共库。

**Spec:** `docs/specs/2026-06-21-loop-boundary-and-control-plane-refactor-design.md`

---

## File Structure

### 新增目录与文件
- Create: `engineering/README.md`
- Create: `engineering/loop/scripts/README.md`
- Create: `engineering/loop/controller/README.md`
- Create: `engineering/loop/contracts/README.md`
- Create: `engineering/loop/workflows/README.md`
- Create: `engineering/loop/contracts/python/loop_contracts/__init__.py`
- Create: `engineering/loop/contracts/python/loop_contracts/failure_codes.py`
- Create: `engineering/loop/contracts/python/loop_contracts/models.py`
- Create: `engineering/loop/contracts/python/tests/test_models.py`
- Create: `engineering/loop/controller/python/loop_controller/__init__.py`
- Create: `engineering/loop/controller/python/loop_controller/state.py`
- Create: `engineering/loop/controller/python/loop_controller/policy.py`
- Create: `engineering/loop/controller/python/loop_controller/engine.py`
- Create: `engineering/loop/controller/python/tests/test_policy.py`
- Create: `engineering/loop/controller/python/tests/test_engine.py`
- Create: `engineering/loop/workflows/python/loop_workflows/__init__.py`
- Create: `engineering/loop/workflows/python/loop_workflows/base.py`
- Create: `engineering/loop/workflows/python/loop_workflows/builtin.py`
- Create: `engineering/loop/workflows/python/tests/test_builtin.py`

### 迁移（严格回迁）
- Move: `engineering/harness/scripts/le.sh` -> `engineering/loop/scripts/le.sh`
- Move: `engineering/harness/scripts/le_runs_cleanup.sh` -> `engineering/loop/scripts/le_runs_cleanup.sh`
- Move: `engineering/harness/scripts/rp5_serial_helper.py` -> `engineering/loop/scripts/rp5_serial_helper.py`
- Move: `engineering/harness/scripts/start_rp5_serial_host.bat` -> `engineering/loop/scripts/start_rp5_serial_host.bat`
- Move: `engineering/harness/workflows/lcview-adb-run/` -> `engineering/loop/workflows/lcview-adb-run/`

### 修改文件
- Modify: `engineering/harness/config/harness-paths.conf`
- Modify: `engineering/harness/config/README.md`
- Modify: `engineering/harness/README.md`
- Modify: `engineering/harness/scripts/README.md`
- Modify: `engineering/harness/workflows/README.md`
- Modify: `engineering/output/README.md`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/harness/tests/test_le_runs_cleanup.sh`
- Modify: `engineering/loop/cases/system/network-adbd-success.yaml`
- Modify: `engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh`（迁移后修改）
- Modify: `engineering/loop/workflows/lcview-adb-run/README.md`（迁移后修改）
- Modify: `engineering/loop/workflows/lcview-adb-run/WORKFLOW.md`（迁移后修改）

### 历史文档策略
- `docs/specs/` 与 `docs/plans/` 下旧设计/旧计划属于历史档案，本计划**不**批量回写旧文档中的旧路径，避免污染历史上下文。
- 仅更新当前活跃导航、当前实现说明、当前测试与当前脚本引用。

---

### Task 1: 建立 engineering 边界总纲与 loop 新目录骨架

**Files:**
- Create: `engineering/README.md`
- Create: `engineering/loop/scripts/README.md`
- Create: `engineering/loop/controller/README.md`
- Create: `engineering/loop/contracts/README.md`
- Create: `engineering/loop/workflows/README.md`
- Modify: `engineering/harness/config/harness-paths.conf`
- Modify: `engineering/harness/config/README.md`

- [ ] **Step 1: 创建 loop 新目录骨架**

Run:
```bash
mkdir -p \
  engineering/loop/scripts \
  engineering/loop/controller/python/loop_controller \
  engineering/loop/controller/python/tests \
  engineering/loop/contracts/python/loop_contracts \
  engineering/loop/contracts/python/tests \
  engineering/loop/workflows/python/loop_workflows \
  engineering/loop/workflows/python/tests
```

Expected:
- `engineering/loop/scripts/`
- `engineering/loop/controller/python/`
- `engineering/loop/contracts/python/`
- `engineering/loop/workflows/python/`

都存在。

- [ ] **Step 2: 写入 `engineering/README.md` 总纲**

Create `engineering/README.md`:

```md
# Engineering

工程能力总目录，负责承载公共工程基础设施与 loop engineering 专属能力；不承载业务源码。

## 一级目录职责

| 目录 | 职责 |
|------|------|
| `engineering/harness/` | 公共 harness engineering 能力层：规则、模板、路径管理、日志观测、跨工程可复用脚本与 workflow |
| `engineering/loop/` | loop engineering 专属能力层：cases、connection、core、scripts、controller、workflows、contracts |
| `engineering/output/` | 本地日志与运行产物目录，不承载实现逻辑 |

## 单向依赖规则

- 允许：`engineering/loop/` 依赖 `engineering/harness/`
- 禁止：`engineering/harness/` 依赖 `engineering/loop/`

## 能力归属判定规则

### 必须放在 `engineering/loop/`
- 包含 loop-specific 语义
- 直接服务 case / suite / connection / transport / session / attempt / rerun / LE runs 生命周期
- 当前仅被 loop 使用
- 抽到 harness 会形成过早公共化

### 允许放在 `engineering/harness/`
- 不含 loop-specific 语义
- 是跨工程基础设施
- 有稳定公共接口
- 不形成 `harness -> loop` 反向依赖

## workflow 归属规则

- 通用工程 workflow -> `engineering/harness/workflows/`
- loop 专属 workflow -> `engineering/loop/workflows/`

## README 同步规则

目录边界、一级目录、核心入口发生变化时，必须同步检查：
- `engineering/README.md`
- `engineering/harness/README.md`
- `engineering/loop/README.md`
```

- [ ] **Step 3: 给 loop 新目录写入口 README**

Create `engineering/loop/scripts/README.md`:

```md
# Loop Scripts

loop engineering 专属脚本入口。

## 文件说明
- `le.sh`：Loop Engineering CLI wrapper
- `le_runs_cleanup.sh`：LE runs 生命周期清理脚本
- `rp5_serial_helper.py`：供 loop host case / workflow 使用的串口辅助工具
- `start_rp5_serial_host.bat`：Windows 端 rp5-serial host daemon 启动器

## 依赖边界
- 允许依赖 `engineering/harness/lib/` 的公共 bootstrap / path / observability 能力
- 禁止把 loop-specific 脚本重新放回 `engineering/harness/scripts/`
```

Create `engineering/loop/controller/README.md`:

```md
# Loop Controller

loop engineering 控制面：session、attempt、状态机、terminate / retry / regression policy。
```

Create `engineering/loop/contracts/README.md`:

```md
# Loop Contracts

loop 控制面 machine-readable contract：SessionState、AttemptState、StageResult、TerminationDecision、FailureCode。
```

Create `engineering/loop/workflows/README.md`:

```md
# Loop Workflows

loop engineering 专属 workflow 与 phase plan。凡直接服务 loop suite / transport / fallback / rerun 的流程都放在此目录，而不是 `engineering/harness/workflows/`。
```

- [ ] **Step 4: 在 `harness-paths.conf` 中增加 loop 目录路径键与 Python 根**

把 `engineering/harness/config/harness-paths.conf` 修改为：

```conf
# --- 工程核心目录 ---
ENGINEERING_DIR="engineering"
HARNESS_DIR="engineering/harness"
LOOP_DIR="engineering/loop"
LOOP_SCRIPTS_DIR="engineering/loop/scripts"
LOOP_WORKFLOWS_DIR="engineering/loop/workflows"
LOOP_CASES_DIR="engineering/loop/cases"
SHELL_LIB_DIR="engineering/harness/lib/shell"
PYTHON_LIB_DIR="engineering/harness/lib/python"
BAT_LIB_DIR="engineering/harness/lib/bat"

# --- Python 包根（PYTHONPATH 用，冒号分隔） ---
PYTHON_PATH_ROOTS="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/workflows/python"
```

- [ ] **Step 5: 更新 `engineering/harness/config/README.md` 的路径配置说明**

把 `engineering/harness/config/README.md` 中 `harness-paths.conf` 的说明替换为：

```md
| [harness-paths.conf](./harness-paths.conf) | 统一路径配置（shell / python / bat 三方共用的单一事实源），定义 `harness/`、`loop/`、`output/` 等工程路径 KEY | 规则 [rules/path-management.md](../rules/path-management.md) (PATH-001) |
```

并在“何时更新”章节追加：

```md
- **新增 loop / harness 目录入口**：若脚本需要新的工程路径 KEY（如 `LOOP_SCRIPTS_DIR`、`LOOP_WORKFLOWS_DIR`），必须先更新 `harness-paths.conf`，再修改脚本引用。
```

- [ ] **Step 6: 验证目录和路径配置**

Run:
```bash
test -f engineering/README.md && \
test -f engineering/loop/scripts/README.md && \
test -f engineering/loop/controller/README.md && \
test -f engineering/loop/contracts/README.md && \
test -f engineering/loop/workflows/README.md && \
grep -q 'LOOP_SCRIPTS_DIR="engineering/loop/scripts"' engineering/harness/config/harness-paths.conf && \
grep -q 'engineering/loop/controller/python' engineering/harness/config/harness-paths.conf && \
echo OK
```

Expected: 输出 `OK`。

---

### Task 2: 严格回迁 loop-specific 脚本并修正活动引用

**Files:**
- Move: `engineering/harness/scripts/le.sh` -> `engineering/loop/scripts/le.sh`
- Move: `engineering/harness/scripts/le_runs_cleanup.sh` -> `engineering/loop/scripts/le_runs_cleanup.sh`
- Move: `engineering/harness/scripts/rp5_serial_helper.py` -> `engineering/loop/scripts/rp5_serial_helper.py`
- Move: `engineering/harness/scripts/start_rp5_serial_host.bat` -> `engineering/loop/scripts/start_rp5_serial_host.bat`
- Modify: `engineering/loop/scripts/le.sh`
- Modify: `engineering/loop/scripts/le_runs_cleanup.sh`
- Modify: `engineering/loop/scripts/start_rp5_serial_host.bat`
- Modify: `engineering/loop/cases/system/network-adbd-success.yaml`
- Modify: `engineering/harness/tests/test_le_runs_cleanup.sh`
- Modify: `engineering/harness/scripts/README.md`
- Modify: `engineering/output/README.md`
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: 执行脚本回迁**

Run:
```bash
mv engineering/harness/scripts/le.sh engineering/loop/scripts/le.sh
mv engineering/harness/scripts/le_runs_cleanup.sh engineering/loop/scripts/le_runs_cleanup.sh
mv engineering/harness/scripts/rp5_serial_helper.py engineering/loop/scripts/rp5_serial_helper.py
mv engineering/harness/scripts/start_rp5_serial_host.bat engineering/loop/scripts/start_rp5_serial_host.bat
```

Expected: 4 个脚本都出现在 `engineering/loop/scripts/` 下。

- [ ] **Step 2: 修正 `engineering/loop/scripts/le.sh` 的 bootstrap 相对路径**

Replace `engineering/loop/scripts/le.sh` with:

```bash
#!/bin/bash
# le.sh — Loop Engineering v2 统一 CLI 入口
# 用法:
#   le.sh run --suite boot-success --fixture <jsonl> --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
#   le.sh run --suite boot-success --host 127.0.0.1 --port 9700 --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../harness/lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../harness/lib/shell/harness_bootstrap.sh"

harness_init "le"

export PYTHONPATH="$(harness_pythonpath)${PYTHONPATH:+:$PYTHONPATH}"

python3 -m loop_core.cli "$@"
rc=$?

# 收尾清理 runs/ 下过期 run-id 子目录（失败不中断主流程）
# 保留份数由环境变量 LE_RUNS_KEEP 控制，默认 20
bash "$SCRIPT_DIR/le_runs_cleanup.sh" --keep "${LE_RUNS_KEEP:-20}" \
    || log_warn "runs 清理失败（不影响本次运行结果，退出码 $rc 已保留）"

harness_exit "$rc"
```

- [ ] **Step 3: 修正 `engineering/loop/scripts/le_runs_cleanup.sh` 的 bootstrap 相对路径**

把 `engineering/loop/scripts/le_runs_cleanup.sh` 头部改为：

```bash
#!/bin/bash
# le_runs_cleanup.sh — 清理 LE 框架 runs/ 下过期 run-id 子目录
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../harness/lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../harness/lib/shell/harness_bootstrap.sh"

harness_init "le-runs-cleanup"
KEEP="${LE_RUNS_KEEP:-20}"
DRY_RUN=false
```

其余逻辑保持不变。

- [ ] **Step 4: 修正 `engineering/loop/scripts/start_rp5_serial_host.bat` 的 path util 相对路径**

把 `engineering/loop/scripts/start_rp5_serial_host.bat` 中这行：

```bat
set "HARNESS_PATH_UTIL=%SCRIPT_DIR%\..\lib\bat\harness_path_util.bat"
```

替换为：

```bat
set "HARNESS_PATH_UTIL=%SCRIPT_DIR%\..\..\harness\lib\bat\harness_path_util.bat"
```

并把文件头说明里的 README 引用改为：

```bat
REM          CMD parsing will fail. See engineering/loop/scripts/README.md.
```

- [ ] **Step 5: 更新 `network-adbd-success.yaml` 中的 helper 路径**

把 `engineering/loop/cases/system/network-adbd-success.yaml` 中 host case 的命令改为：

```yaml
command: "DEV_IP=$(PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700); [ -z \"$DEV_IP\" ] && { echo NO_IP_FOUND; exit 1; }; echo \"DEV_IP=$DEV_IP\"; adb disconnect $DEV_IP:5555 >/dev/null 2>&1; adb connect $DEV_IP:5555"
```

- [ ] **Step 6: 更新 `test_le_runs_cleanup.sh` 的被测脚本路径与沙箱布局**

在 `engineering/harness/tests/test_le_runs_cleanup.sh` 中做以下替换：

```bash
CLEANUP_SCRIPT="$REPO_ROOT/engineering/loop/scripts/le_runs_cleanup.sh"
```

把 `setup_sandbox()` 改为：

```bash
setup_sandbox() {
    local sandbox="$1"
    mkdir -p "$sandbox/engineering/harness/lib/shell"
    mkdir -p "$sandbox/engineering/harness/config"
    mkdir -p "$sandbox/engineering/loop/scripts"
    mkdir -p "$sandbox/engineering/output/runs"
    touch "$sandbox/AGENTS.md"

    cp "$REPO_ROOT/engineering/harness/lib/shell/harness_bootstrap.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_observability.sh" \
       "$REPO_ROOT/engineering/harness/lib/shell/harness_path_util.sh" \
       "$sandbox/engineering/harness/lib/shell/"
    cp "$REPO_ROOT/engineering/harness/config/harness-paths.conf" \
       "$sandbox/engineering/harness/config/"
    cp "$CLEANUP_SCRIPT" "$sandbox/engineering/loop/scripts/le_runs_cleanup.sh"

    printf '%s\n' "$sandbox"
}
```

把 `run_cleanup()` 改为：

```bash
run_cleanup() {
    local sandbox="$1"
    shift
    env -i PATH="$PATH" HOME="$HOME" bash "$sandbox/engineering/loop/scripts/le_runs_cleanup.sh" "$@"
}
```

---

### Task 3: 严格回迁 `lcview-adb-run` workflow 并修正 loop 入口依赖

**Files:**
- Move: `engineering/harness/workflows/lcview-adb-run/` -> `engineering/loop/workflows/lcview-adb-run/`
- Modify: `engineering/loop/workflows/lcview-adb-run/WORKFLOW.md`
- Modify: `engineering/loop/workflows/lcview-adb-run/README.md`
- Modify: `engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh`
- Modify: `engineering/harness/workflows/README.md`
- Modify: `engineering/harness/README.md`
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: 回迁 `lcview-adb-run` 目录**

Run:
```bash
mv engineering/harness/workflows/lcview-adb-run engineering/loop/workflows/lcview-adb-run
```

Expected: `engineering/loop/workflows/lcview-adb-run/` 存在，且包含 `README.md`、`WORKFLOW.md`、`run_lcview_adb_suite.sh`。

- [ ] **Step 2: 修正 workflow 脚本对 harness bootstrap 与 loop 脚本的引用**

Replace `engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh` with:

```bash
#!/bin/bash
# lcview-adb-run — serial bootstrap → adb feature run → serial fallback
# 用法:
#   run_lcview_adb_suite.sh --serial-host 127.0.0.1 --serial-port 9700 [--adb-endpoint 192.168.1.55:5555] [--artifacts-dir <dir>]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../harness/lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../harness/lib/shell/harness_bootstrap.sh"

harness_init "lcview-adb-run"

SERIAL_HOST="127.0.0.1"
SERIAL_PORT="9700"
ADB_ENDPOINT=""
ARTIFACTS_DIR="$(harness_path RUNS_DIR)/lcview-adb-run"
SERIAL_PROFILE="$(harness_path ENGINEERING_DIR)/loop/connection/profiles/devices/rp5/default.json"
ADB_PROFILE="$(harness_path ENGINEERING_DIR)/loop/connection/profiles/devices/rp5/adb.json"
CASE_DIR="$(harness_path ENGINEERING_DIR)/loop/cases"
BOOTSTRAP_SUITE="$(harness_path ENGINEERING_DIR)/loop/cases/system/network-adbd-success.yaml"
FEATURE_SUITE="$(harness_path ENGINEERING_DIR)/loop/cases/features/lcview/end_to_end.yaml"
LOOP_SCRIPTS_DIR="$(harness_path LOOP_SCRIPTS_DIR)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial-host) SERIAL_HOST="$2"; shift 2 ;;
    --serial-port) SERIAL_PORT="$2"; shift 2 ;;
    --adb-endpoint) ADB_ENDPOINT="$2"; shift 2 ;;
    --artifacts-dir) ARTIFACTS_DIR="$2"; shift 2 ;;
    --serial-profile) SERIAL_PROFILE="$2"; shift 2 ;;
    --adb-profile) ADB_PROFILE="$2"; shift 2 ;;
    *) log_error "unknown arg: $1"; harness_exit 2 ;;
  esac
done

BOOTSTRAP_OUT="$ARTIFACTS_DIR/bootstrap"
FEATURE_OUT="$ARTIFACTS_DIR/feature"
FALLBACK_OUT="$ARTIFACTS_DIR/fallback"
mkdir -p "$BOOTSTRAP_OUT" "$FEATURE_OUT" "$FALLBACK_OUT"

step_begin "bootstrap" "run serial network-adbd bootstrap"
bash "$LOOP_SCRIPTS_DIR/le.sh" run \
  --suite "$BOOTSTRAP_SUITE" \
  --host "$SERIAL_HOST" \
  --port "$SERIAL_PORT" \
  --device-profile "$SERIAL_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$BOOTSTRAP_OUT"
bootstrap_rc=$?
step_end "bootstrap" "$bootstrap_rc"

if [[ $bootstrap_rc -ne 0 ]]; then
  log_error "BOOTSTRAP_FAIL (rc=$bootstrap_rc)"
  harness_status_emit FAIL "bootstrap"
  harness_exit "$bootstrap_rc"
fi

if [[ -z "$ADB_ENDPOINT" ]]; then
  step_begin "discover-adb-endpoint" "discover adb endpoint from serial helper"
  DISCOVERED_IP="$(python3 "$LOOP_SCRIPTS_DIR/rp5_serial_helper.py" device-ip --host "$SERIAL_HOST" --port "$SERIAL_PORT" 2>/dev/null || true)"
  if [[ -z "$DISCOVERED_IP" || "$DISCOVERED_IP" == "NO_IP_FOUND" ]]; then
    log_error "ADB_CONNECT_FAIL: cannot discover device IP"
    harness_status_emit FAIL "discover-adb-endpoint"
    harness_exit 1
  fi
  ADB_ENDPOINT="${DISCOVERED_IP}:5555"
  step_end "discover-adb-endpoint" 0
fi

log_info "adb endpoint: $ADB_ENDPOINT"

step_begin "feature" "run lcview adb feature suite"
bash "$LOOP_SCRIPTS_DIR/le.sh" run \
  --suite "$FEATURE_SUITE" \
  --device-profile "$ADB_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$FEATURE_OUT" \
  --adb-endpoint "$ADB_ENDPOINT"
feature_rc=$?
step_end "feature" "$feature_rc"

if [[ $feature_rc -ne 0 ]]; then
  log_warn "feature run failed (rc=$feature_rc), collecting serial fallback evidence"
  step_begin "fallback" "collect serial fallback context"
  bash "$LOOP_SCRIPTS_DIR/le.sh" run \
    --suite "$(harness_path ENGINEERING_DIR)/loop/cases/system/boot-success.yaml" \
    --host "$SERIAL_HOST" \
    --port "$SERIAL_PORT" \
    --device-profile "$SERIAL_PROFILE" \
    --case-dirs "$CASE_DIR" \
    --artifacts-dir "$FALLBACK_OUT"
  step_end "fallback" 0
fi

log_result "lcview-adb-run" "$feature_rc" "feature_rc"
harness_exit "$feature_rc"
```

- [ ] **Step 3: 更新 workflow 文档归属说明**

Replace `engineering/loop/workflows/lcview-adb-run/README.md` with:

```md
# lcview-adb-run

loop engineering 专属多阶段 workflow：串口 bootstrap 后切换 adb 执行 `lcview` feature suite，并在失败时补采 serial fallback evidence。

详细流程见 [WORKFLOW.md](./WORKFLOW.md)。
```

Replace `engineering/loop/workflows/lcview-adb-run/WORKFLOW.md` with:

```md
# lcview-adb-run Workflow

## 目标

提供 loop 专属单入口 workflow：
1. 用 serial profile 跑 `system/network-adbd-success.yaml`（bootstrap）
2. 提取 adb endpoint（从 serial helper 或参数直传）
3. 用 adb profile 跑 `features/lcview/end_to_end.yaml`（feature run）
4. adb run 失败时补采 serial context（fallback）
5. 汇总 bootstrap / feature artifacts 与 failure code

## 输入参数

- `--serial-host`（默认 127.0.0.1）
- `--serial-port`（默认 9700）
- `--adb-endpoint`（可选；为空时自动从 serial helper 发现）
- `--artifacts-dir`（默认 `engineering/output/runs/lcview-adb-run`）
- `--serial-profile`（默认 rp5/default.json）
- `--adb-profile`（默认 rp5/adb.json）

## 失败分型

| failure code | 含义 |
|---|---|
| `BOOTSTRAP_FAIL` | bootstrap 阶段失败（串口未通 / WiFi 未连 / adbd 未启动） |
| `ADB_CONNECT_FAIL` | adb endpoint 缺失或 adb connect 失败 |
| `ADB_EXEC_FAIL` | adb suite 运行中命令执行异常 |
| `LCVIEW_PREREQ_FAIL` | lcview 前提不满足（服务/ schema / 目录） |
| `LCVIEW_TRIGGER_FAIL` | trigger 动作执行失败 |
| `LCVIEW_PIPELINE_FAIL` | jsonl 未生成或内容为空 |
| `LCVIEW_EVIDENCE_FAIL` | 关键 evidence pull 失败 |

## 归属规则

该 workflow 属于 loop engineering 专属 phase plan，不得放回 `engineering/harness/workflows/`。

## 脚本入口

`run_lcview_adb_suite.sh`
```

- [ ] **Step 4: 更新 harness/loop README 中的 workflow 导航**

在 `engineering/harness/workflows/README.md` 中删除这一行：

```md
| [lcview-adb-run](./lcview-adb-run/) | 串口 bootstrap 后切换 adb 执行 lcview | 两阶段 transport 编排 + fallback evidence | `run_lcview_adb_suite.sh` |
```

在 `engineering/harness/README.md` 的快速导航表中，把：

```md
| 跑 lcview 的 serial→adb 双阶段验收 | [workflows/lcview-adb-run/](./workflows/lcview-adb-run/) |
```

替换为：

```md
| 跑 lcview 的 serial→adb 双阶段验收 | [../loop/workflows/lcview-adb-run/](../loop/workflows/lcview-adb-run/) |
```

在 `engineering/loop/README.md` 的“目录结构”代码块下方新增：

```md
> loop 专属 workflow 位于 `engineering/loop/workflows/`，当前包含 `lcview-adb-run/` 多阶段验证样板。
```

- [ ] **Step 5: 验证 workflow 回迁后的关键入口**

Run:
```bash
test -f engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh && \
grep -q 'engineering/loop/workflows/' engineering/harness/README.md && \
! grep -q 'lcview-adb-run' engineering/harness/workflows/README.md && \
grep -q 'LOOP_SCRIPTS_DIR' engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh && \
echo OK
```

Expected: 输出 `OK`。

---

### Task 4: 更新当前活跃说明文档，收敛 loop / harness / output 语义

**Files:**
- Modify: `engineering/harness/README.md`
- Modify: `engineering/harness/scripts/README.md`
- Modify: `engineering/output/README.md`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/WORKFLOW.md`

- [ ] **Step 1: 更新 `engineering/harness/README.md` 的职责说明与快速导航**

在 `engineering/harness/README.md` 中做以下修改：

1. 第 3 行段落中追加一句：

```md
`engineering/harness/` 只承载公共 harness engineering 能力，不承载 loop-specific case / workflow / controller / session / LE CLI。
```

2. 在“目录说明”表后追加一段：

```md
## 与 `engineering/loop/` 的边界

- `engineering/harness/`：公共规则、公共 workflow、公共脚本基础设施
- `engineering/loop/`：loop engineering 专属 case / connection / core / scripts / workflows / controller / contracts
- 依赖方向固定为 `loop -> harness`，禁止 `harness -> loop`
```

3. 保留对 `../loop/workflows/lcview-adb-run/` 的导航，不再把它列为 harness workflow 本体。

- [ ] **Step 2: 重写 `engineering/harness/scripts/README.md`，移除已回迁脚本章节**

把 `engineering/harness/scripts/README.md` 的文件说明区收敛为仅包含 harness 公共脚本，例如：

```md
# Scripts

独立一次性脚本——属于 harness 公共工程工具，而不是 loop engineering 专属入口。

## 文件说明
- [`mk_rpi5_full_image.sh`](./mk_rpi5_full_image.sh) — 树莓派 5 AOSP 一键编译打包脚本。
- [`validate_harness_docs.sh`](./validate_harness_docs.sh) — 文档/契约层静态校验。
- [`validate_harness_scripts.sh`](./validate_harness_scripts.sh) — bash 合规校验。
- [`validate_harness_config.sh`](./validate_harness_config.sh) — 配置层校验。

## 已迁出到 `engineering/loop/scripts/`
- `le.sh`
- `le_runs_cleanup.sh`
- `rp5_serial_helper.py`
- `start_rp5_serial_host.bat`
```

保留原有 Windows `.bat` 通用格式注意事项，但把路径示例替换为 `engineering/loop/scripts/start_rp5_serial_host.bat`。

- [ ] **Step 3: 更新 `engineering/output/README.md` 的脚本路径**

把 `engineering/output/README.md` 中两处旧路径替换为：

```md
由 Windows 端 `engineering/loop/scripts/start_rp5_serial_host.bat` 触发写入。
```

```md
`le.sh` 每次运行结束时自动调用 [`le_runs_cleanup.sh`](../loop/scripts/le_runs_cleanup.sh) 收敛产物规模：
```

并把手动触发示例替换为：

```md
- **手动触发**：`bash engineering/loop/scripts/le_runs_cleanup.sh --keep 20 --dry-run`
```

- [ ] **Step 4: 更新 `engineering/loop/README.md` 的入口、结构与示例路径**

在 `engineering/loop/README.md` 中执行以下替换：

1. 目录结构代码块改为：

```md
engineering/loop/
├── core/python/loop_core/       LE 单次 attempt 执行内核
├── cases/                       声明式用例（YAML）
├── templates/                   AI 生成约束模板
├── connection/                  连接层（provider）
├── scripts/                     loop 专属脚本入口
├── controller/                  loop 控制面（session / policy）
├── workflows/                   loop 专属 workflow / phase plan
└── contracts/                   loop 控制面契约
```

2. 把：

```md
> CLI 入口脚本已移至 `engineering/harness/scripts/le.sh`
> Windows Host 启动脚本已移至 `engineering/harness/scripts/start_rp5_serial_host.bat`
```

替换为：

```md
> CLI 入口脚本位于 `engineering/loop/scripts/le.sh`（亦可通过 opencode slash command `/le` 触发）
> Windows Host 启动脚本位于 `engineering/loop/scripts/start_rp5_serial_host.bat`
> loop 专属 workflow 位于 `engineering/loop/workflows/`，当前包含 `lcview-adb-run/` 多阶段验证样板
```

3. 把所有示例里的：

```bash
bash engineering/harness/scripts/le.sh run \
```

替换为：

```bash
bash engineering/loop/scripts/le.sh run \
```

- [ ] **Step 5: 更新 `engineering/loop/WORKFLOW.md` 的分层职责表**

把 `engineering/loop/WORKFLOW.md` 的“分层职责”表替换为：

```md
| 层 | 职责 |
|----|------|
| opencode (AI) | 生成用例 / 分析证据 / 收敛候选修复方向 |
| `engineering/loop/controller/` | loop session / terminate-retry-regression policy / workflow 调度 |
| `engineering/loop/workflows/` | loop 专属 phase plan / bootstrap / verify / fallback / rerun |
| `loop_core` | 单次 attempt：用例加载 / 断言 / 执行 / 证据输出 |
| `cases/*.yaml` | 场景定义（声明式，零 Python） |
| `connection` | 传输层（串口 / ADB） |
| `engineering/harness/` | 公共规则、路径管理、脚本 bootstrap、日志与 observability 基础设施 |
```

并在“遗留点”列表后追加：

```md
> `loop_ctrl` 后续落点为 `engineering/loop/controller/`，不进入 `engineering/harness/`。
```

---

### Task 5: 先以 TDD 建立 contracts 最小骨架

**Files:**
- Create: `engineering/loop/contracts/python/loop_contracts/__init__.py`
- Create: `engineering/loop/contracts/python/loop_contracts/failure_codes.py`
- Create: `engineering/loop/contracts/python/loop_contracts/models.py`
- Create: `engineering/loop/contracts/python/tests/test_models.py`
- Modify: `engineering/harness/config/harness-paths.conf`

- [ ] **Step 1: 先写 contracts 失败测试**

Create `engineering/loop/contracts/python/tests/test_models.py`:

```python
from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import AttemptState, SessionState, StageResult, TerminationDecision


def test_stage_result_defaults():
    result = StageResult(stage_name="run", status="PASS")
    assert result.failure_code == FailureCode.NONE
    assert result.summary == ""
    assert result.artifacts == []
    assert result.next_action_hint == ""


def test_attempt_state_holds_stage_results():
    result = StageResult(stage_name="run", status="FAIL", failure_code=FailureCode.RUN_FAILED)
    attempt = AttemptState(attempt_index=2, stage_results=[result], attempt_decision="retry")
    assert attempt.attempt_index == 2
    assert attempt.stage_results[0].failure_code == FailureCode.RUN_FAILED
    assert attempt.attempt_decision == "retry"


def test_session_state_tracks_attempts():
    session = SessionState(
        session_id="sess-001",
        workflow_id="single_run_verify",
        target="system.boot",
        max_attempts=5,
    )
    assert session.current_attempt == 0
    assert session.status == "PENDING"
    assert session.attempts == []


def test_termination_decision_flags_retry_and_escalation():
    decision = TerminationDecision(
        decision="STOP",
        reason_code=FailureCode.REGRESSION_DETECTED,
        reason_summary="new severe failure",
        can_retry=False,
        should_escalate=True,
    )
    assert decision.should_escalate is True
    assert decision.can_retry is False
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/contracts/python" \
python3 -m pytest engineering/loop/contracts/python/tests/test_models.py -v
```

Expected: FAIL，报 `ModuleNotFoundError: No module named 'loop_contracts'`。

- [ ] **Step 3: 写 contracts 最小实现**

Create `engineering/loop/contracts/python/loop_contracts/failure_codes.py`:

```python
from enum import StrEnum


class FailureCode(StrEnum):
    NONE = "NONE"
    RUN_FAILED = "RUN_FAILED"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    DEPLOY_FATAL = "DEPLOY_FATAL"
    SESSION_STATE_ERROR = "SESSION_STATE_ERROR"
```

Create `engineering/loop/contracts/python/loop_contracts/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from loop_contracts.failure_codes import FailureCode


@dataclass
class StageResult:
    stage_name: str
    status: str
    failure_code: FailureCode = FailureCode.NONE
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    next_action_hint: str = ""


@dataclass
class AttemptState:
    attempt_index: int
    stage_results: list[StageResult] = field(default_factory=list)
    run_result_ref: str = ""
    diagnosis_result_ref: str = ""
    patch_result_ref: str = ""
    deploy_result_ref: str = ""
    verify_result_ref: str = ""
    attempt_decision: str = ""


@dataclass
class SessionState:
    session_id: str
    workflow_id: str
    target: str
    max_attempts: int
    current_attempt: int = 0
    status: str = "PENDING"
    termination_reason: str = ""
    attempts: list[AttemptState] = field(default_factory=list)


@dataclass
class TerminationDecision:
    decision: str
    reason_code: FailureCode
    reason_summary: str
    can_retry: bool
    should_escalate: bool
```

Create `engineering/loop/contracts/python/loop_contracts/__init__.py`:

```python
from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import AttemptState, SessionState, StageResult, TerminationDecision

__all__ = [
    "AttemptState",
    "FailureCode",
    "SessionState",
    "StageResult",
    "TerminationDecision",
]
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/contracts/python" \
python3 -m pytest engineering/loop/contracts/python/tests/test_models.py -v
```

Expected: `4 passed`。

- [ ] **Step 5: 提交 contracts 骨架**

```bash
git add \
  engineering/loop/contracts/python/loop_contracts/__init__.py \
  engineering/loop/contracts/python/loop_contracts/failure_codes.py \
  engineering/loop/contracts/python/loop_contracts/models.py \
  engineering/loop/contracts/python/tests/test_models.py \
  engineering/harness/config/harness-paths.conf

git commit -m "feat(loop): add control-plane contracts skeleton"
```

---

### Task 6: 先以 TDD 建立 controller 最小 policy 与 engine 骨架

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/__init__.py`
- Create: `engineering/loop/controller/python/loop_controller/state.py`
- Create: `engineering/loop/controller/python/loop_controller/policy.py`
- Create: `engineering/loop/controller/python/loop_controller/engine.py`
- Create: `engineering/loop/controller/python/tests/test_policy.py`
- Create: `engineering/loop/controller/python/tests/test_engine.py`

- [ ] **Step 1: 先写 policy 失败测试**

Create `engineering/loop/controller/python/tests/test_policy.py`:

```python
from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import StageResult
from loop_controller.policy import decide_termination


def test_pass_result_stops_session():
    decision = decide_termination(
        max_attempts=5,
        current_attempt=1,
        latest_stage=StageResult(stage_name="verify", status="PASS"),
        previous_failure_codes=[],
    )
    assert decision.decision == "STOP"
    assert decision.reason_code == FailureCode.NONE


def test_exceed_max_attempts_stops_session():
    decision = decide_termination(
        max_attempts=2,
        current_attempt=3,
        latest_stage=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=[FailureCode.RUN_FAILED],
    )
    assert decision.decision == "STOP"
    assert decision.reason_code == FailureCode.REPEATED_FAILURE


def test_same_failure_twice_stops_session():
    decision = decide_termination(
        max_attempts=5,
        current_attempt=2,
        latest_stage=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=[FailureCode.RUN_FAILED],
    )
    assert decision.decision == "STOP"
    assert decision.reason_code == FailureCode.REPEATED_FAILURE


def test_first_failure_allows_retry():
    decision = decide_termination(
        max_attempts=5,
        current_attempt=1,
        latest_stage=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=[],
    )
    assert decision.decision == "RETRY"
    assert decision.can_retry is True
```

Create `engineering/loop/controller/python/tests/test_engine.py`:

```python
from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import SessionState, StageResult
from loop_controller.engine import apply_stage_result


def test_apply_stage_result_appends_attempt_and_updates_status():
    session = SessionState(
        session_id="sess-001",
        workflow_id="single_run_verify",
        target="system.boot",
        max_attempts=5,
    )

    updated = apply_stage_result(
        session,
        attempt_index=1,
        stage_result=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        decision="RETRY",
    )

    assert updated.current_attempt == 1
    assert updated.status == "RETRY"
    assert updated.attempts[-1].stage_results[-1].failure_code == FailureCode.RUN_FAILED
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
python3 -m pytest \
  engineering/loop/controller/python/tests/test_policy.py \
  engineering/loop/controller/python/tests/test_engine.py -v
```

Expected: FAIL，报 `ModuleNotFoundError: No module named 'loop_controller'`。

- [ ] **Step 3: 写 controller 最小实现**

Create `engineering/loop/controller/python/loop_controller/policy.py`:

```python
from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import StageResult, TerminationDecision



def decide_termination(*, max_attempts: int, current_attempt: int, latest_stage: StageResult, previous_failure_codes: list[FailureCode]) -> TerminationDecision:
    if latest_stage.status == "PASS":
        return TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.NONE,
            reason_summary="verification passed",
            can_retry=False,
            should_escalate=False,
        )

    if current_attempt > max_attempts:
        return TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.REPEATED_FAILURE,
            reason_summary="max attempts exceeded",
            can_retry=False,
            should_escalate=True,
        )

    if previous_failure_codes and latest_stage.failure_code == previous_failure_codes[-1]:
        return TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.REPEATED_FAILURE,
            reason_summary="same failure repeated",
            can_retry=False,
            should_escalate=True,
        )

    return TerminationDecision(
        decision="RETRY",
        reason_code=latest_stage.failure_code,
        reason_summary="retry allowed",
        can_retry=True,
        should_escalate=False,
    )
```

Create `engineering/loop/controller/python/loop_controller/engine.py`:

```python
from loop_contracts.models import AttemptState, SessionState, StageResult


def apply_stage_result(session: SessionState, *, attempt_index: int, stage_result: StageResult, decision: str) -> SessionState:
    attempt = AttemptState(attempt_index=attempt_index, stage_results=[stage_result], attempt_decision=decision.lower())
    session.attempts.append(attempt)
    session.current_attempt = attempt_index
    session.status = decision
    return session
```

Create `engineering/loop/controller/python/loop_controller/state.py`:

```python
from loop_contracts.models import SessionState


def new_session(session_id: str, workflow_id: str, target: str, max_attempts: int) -> SessionState:
    return SessionState(
        session_id=session_id,
        workflow_id=workflow_id,
        target=target,
        max_attempts=max_attempts,
    )
```

Create `engineering/loop/controller/python/loop_controller/__init__.py`:

```python
from loop_controller.engine import apply_stage_result
from loop_controller.policy import decide_termination
from loop_controller.state import new_session

__all__ = ["apply_stage_result", "decide_termination", "new_session"]
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
python3 -m pytest \
  engineering/loop/controller/python/tests/test_policy.py \
  engineering/loop/controller/python/tests/test_engine.py -v
```

Expected: `5 passed`。

- [ ] **Step 5: 提交 controller 骨架**

```bash
git add \
  engineering/loop/controller/python/loop_controller/__init__.py \
  engineering/loop/controller/python/loop_controller/state.py \
  engineering/loop/controller/python/loop_controller/policy.py \
  engineering/loop/controller/python/loop_controller/engine.py \
  engineering/loop/controller/python/tests/test_policy.py \
  engineering/loop/controller/python/tests/test_engine.py

git commit -m "feat(loop): add controller policy skeleton"
```

---

### Task 7: 先以 TDD 建立 workflow 最小骨架并接入 `lcview-adb-run` 语义

**Files:**
- Create: `engineering/loop/workflows/python/loop_workflows/__init__.py`
- Create: `engineering/loop/workflows/python/loop_workflows/base.py`
- Create: `engineering/loop/workflows/python/loop_workflows/builtin.py`
- Create: `engineering/loop/workflows/python/tests/test_builtin.py`

- [ ] **Step 1: 先写 workflow 失败测试**

Create `engineering/loop/workflows/python/tests/test_builtin.py`:

```python
from loop_workflows.builtin import MultiPhaseVerifyWorkflow, SingleRunVerifyWorkflow


def test_single_run_verify_exposes_workflow_id():
    workflow = SingleRunVerifyWorkflow()
    assert workflow.workflow_id == "single_run_verify"


def test_multi_phase_verify_exposes_expected_phases():
    workflow = MultiPhaseVerifyWorkflow()
    assert workflow.workflow_id == "multi_phase_verify"
    assert workflow.phases == ["bootstrap", "feature", "fallback"]
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/python" \
python3 -m pytest engineering/loop/workflows/python/tests/test_builtin.py -v
```

Expected: FAIL，报 `ModuleNotFoundError: No module named 'loop_workflows'`。

- [ ] **Step 3: 写 workflow 最小实现**

Create `engineering/loop/workflows/python/loop_workflows/base.py`:

```python
from dataclasses import dataclass, field


@dataclass
class WorkflowDefinition:
    workflow_id: str
    phases: list[str] = field(default_factory=list)
```

Create `engineering/loop/workflows/python/loop_workflows/builtin.py`:

```python
from loop_workflows.base import WorkflowDefinition


class SingleRunVerifyWorkflow(WorkflowDefinition):
    def __init__(self) -> None:
        super().__init__(workflow_id="single_run_verify", phases=["run", "verify"])


class MultiPhaseVerifyWorkflow(WorkflowDefinition):
    def __init__(self) -> None:
        super().__init__(workflow_id="multi_phase_verify", phases=["bootstrap", "feature", "fallback"])
```

Create `engineering/loop/workflows/python/loop_workflows/__init__.py`:

```python
from loop_workflows.base import WorkflowDefinition
from loop_workflows.builtin import MultiPhaseVerifyWorkflow, SingleRunVerifyWorkflow

__all__ = ["WorkflowDefinition", "SingleRunVerifyWorkflow", "MultiPhaseVerifyWorkflow"]
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/python" \
python3 -m pytest engineering/loop/workflows/python/tests/test_builtin.py -v
```

Expected: `2 passed`。

- [ ] **Step 5: 提交 workflow 骨架**

```bash
git add \
  engineering/loop/workflows/python/loop_workflows/__init__.py \
  engineering/loop/workflows/python/loop_workflows/base.py \
  engineering/loop/workflows/python/loop_workflows/builtin.py \
  engineering/loop/workflows/python/tests/test_builtin.py

git commit -m "feat(loop): add workflow skeleton"
```

---

### Task 8: 统一回归验证与文档校验

**Files:**
- Verify only: 本计划涉及全部迁移文件、README、tests、contracts/controller/workflows 骨架

- [ ] **Step 1: 运行 `le_runs_cleanup` 测试**

Run:
```bash
bash engineering/harness/tests/test_le_runs_cleanup.sh
```

Expected: 输出全部通过。

- [ ] **Step 2: 运行 contracts / controller / workflows 单测**

Run:
```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/workflows/python" \
python3 -m pytest \
  engineering/loop/contracts/python/tests/test_models.py \
  engineering/loop/controller/python/tests/test_policy.py \
  engineering/loop/controller/python/tests/test_engine.py \
  engineering/loop/workflows/python/tests/test_builtin.py -v
```

Expected: 全部通过。

- [ ] **Step 3: 做活动引用路径扫描，确认旧路径已从活跃文档中移除**

Run:
```bash
python3 - <<'PY'
from pathlib import Path
checks = {
    'engineering/harness/scripts/le.sh': [
        Path('engineering/loop/README.md'),
        Path('engineering/output/README.md'),
        Path('engineering/harness/scripts/README.md'),
    ],
    'engineering/harness/scripts/le_runs_cleanup.sh': [
        Path('engineering/output/README.md'),
        Path('engineering/harness/scripts/README.md'),
    ],
    'engineering/harness/scripts/rp5_serial_helper.py': [
        Path('engineering/loop/cases/system/network-adbd-success.yaml'),
    ],
    'engineering/harness/scripts/start_rp5_serial_host.bat': [
        Path('engineering/loop/README.md'),
        Path('engineering/output/README.md'),
    ],
    'engineering/harness/workflows/lcview-adb-run': [
        Path('engineering/harness/README.md'),
        Path('engineering/harness/workflows/README.md'),
        Path('engineering/loop/README.md'),
    ],
}
for needle, files in checks.items():
    for file in files:
        text = file.read_text(encoding='utf-8')
        if needle in text:
            raise SystemExit(f'FOUND stale path {needle} in {file}')
print('OK')
PY
```

Expected: 输出 `OK`。

- [ ] **Step 4: 检查 Windows `.bat` 文件仍满足 ASCII + CRLF 约束**

Run:
```bash
python3 - <<'PY'
from pathlib import Path
p = Path('engineering/loop/scripts/start_rp5_serial_host.bat')
data = p.read_bytes()
non_ascii = sum(1 for b in data if b > 127)
lf_only = data.count(b'\n') - data.count(b'\r\n')
print(f'non_ascii={non_ascii}')
print(f'lf_only={lf_only}')
if non_ascii != 0 or lf_only != 0:
    raise SystemExit(1)
PY
```

Expected:
- `non_ascii=0`
- `lf_only=0`

- [ ] **Step 5: 检查 git 变更范围仅包含计划内文件**

Run:
```bash
git status --short
```

Expected:
- 仅出现本计划涉及的迁移、新增、README/test 更新文件
- 不出现与本任务无关的额外改动

- [ ] **Step 6: 最终提交**

```bash
git add engineering/README.md \
        engineering/loop \
        engineering/harness/config/harness-paths.conf \
        engineering/harness/config/README.md \
        engineering/harness/README.md \
        engineering/harness/scripts/README.md \
        engineering/harness/workflows/README.md \
        engineering/harness/tests/test_le_runs_cleanup.sh \
        engineering/output/README.md

git commit -m "refactor(loop): reclaim loop-specific engineering assets"
```

---

## Self-Review

### Spec coverage
- 顶层边界总纲：Task 1
- 严格回迁 loop-specific scripts：Task 2
- 严格回迁 loop-specific workflow：Task 3
- README / 当前说明同步：Task 4
- contracts 最小骨架：Task 5
- controller terminate/retry 最小骨架：Task 6
- workflows 最小骨架：Task 7
- 统一验证：Task 8

### Placeholder scan
- 未使用 TBD / TODO / “后续补充” 作为任务步骤替代品。
- 所有代码步骤均给出具体文件内容或替换片段。
- 所有验证步骤给出明确命令与期望结果。

### Type consistency
- `FailureCode`、`StageResult`、`AttemptState`、`SessionState`、`TerminationDecision` 在 Task 5-7 中名称一致。
- `SingleRunVerifyWorkflow` / `MultiPhaseVerifyWorkflow` 的命名与 spec 中 workflow 骨架保持一致。
- `LOOP_SCRIPTS_DIR` / `LOOP_WORKFLOWS_DIR` / `LOOP_CASES_DIR` 与 `harness-paths.conf` 中新增键保持一致。

---

Plan complete and saved to `docs/plans/2026-06-21-loop-boundary-and-control-plane-refactor.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

<!-- PLAN_APPEND_1 -->
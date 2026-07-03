# LcHarness Phase 2 Control Plane & Repo Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the LcHarness central control plane — attach / inject / validate / status / detach / reconcile minimal closed loop — with a local Repo Registry as the single source of truth, implemented within the current repo under `engineering/harness/control-plane/`.

**Architecture:** Phase 2 builds on Phase 1's explicit layering and layer map. All control-plane scripts live in `engineering/harness/control-plane/`; the registry YAML lives at `~/.local/share/lcharness/registry.yaml` (zero tracked contamination). The current AndroidSystemEnhance repo serves as the first target repo for end-to-end validation.

**Tech Stack:** Bash (harness observability lib), Python3 (YAML read/write for registry), YAML (registry format), Markdown (README, WORKFLOW.md)

**Registry root:** `~/.local/share/lcharness/`
**Control-plane dir:** `engineering/harness/control-plane/`

---

## Scope and success criteria

### In scope
- `~/.local/share/lcharness/` directory structure + `registry.yaml` format definition
- 5 control-plane scripts (repo-registry / attach / status / inject / reconcile)
- Full 7-state lifecycle: detached → attached → injected → healthy ↔ stale ↔ broken → re-attach/detach
- Overlay directory creation with `.lcharness-overlay` marker file (Phase 2: no symlinks, no capability projection)
- Health check (validate) logic with stale/broken detection
- Repo registry file lock (flock/mkdir fallback) for concurrent write safety
- Tests for all 5 scripts covering normal, error, and edge cases
- README updates: control-plane README, scripts README, tests README, harness README
- Layer-map update: add `engineering/harness/control-plane/` entry
- Update manifest.yaml if needed for new access context

### Out of scope
- No standalone `LcHarness` repo extraction yet
- No symbolic-link-based overlay capability projection (Phase 3)
- No profile/pack resolution engine (Phase 3)
- No multi-repo orchestration beyond the first target repo
- No `LC_HARNESS_ROOT` environment variable or system-wide install

### Done means
- `engineering/harness/control-plane/` exists with 5 scripts + 1 README
- `lc-repo-registry.sh add / remove / list / get / update / exists` all work
- `lc-attach.sh <path>` registers repo, creates overlay, validates → healthy
- `lc-status.sh [id]` shows state table
- `lc-inject.sh <id>` creates overlay structure
- `lc-validate.sh <id>` returns healthy/stale/broken with reasons
- `lc-reconcile.sh <id>` repairs stale/broken to healthy
- `lc-detach.sh <id>` cleans overlay + removes registry entry
- All 7 test scripts exist under `tests/` and pass
- `run_all_tests.sh` includes all Phase 2 tests
- Layer-map updated, READMEs synced
- Full validation set passes

---

## Task 1: Create control-plane directory structure and README

**Files:**
- Create: `engineering/harness/control-plane/README.md`
- Modify: `engineering/harness/README.md` (add nav entry and directory listing)
- Modify: `engineering/harness/scripts/README.md` (note control-plane lives in sibling dir)

- [ ] **Step 1: Create the control-plane README**

Create `engineering/harness/control-plane/README.md`:

```markdown
# Control Plane

> LcHarness 中央控制面：Repo Registry 管理与生命周期控制

## 定位

- **是什么**：LcHarness 的集中控制面，负责 repo 注册、overlay 注入、状态管理、健康检查与 reconciliation
- **职责边界**：
  - 控制面动作：attach / inject / validate / status / detach / reconcile
  - 不承载 core 层能力（rules / config / lib / validator）
  - 不承载 pack/profile 业务语义
- **真相源**：
  - Repo Registry 保存在 `~/.local/share/lcharness/registry.yaml`（本地，不受 git 跟踪）
  - Overlay 缓存在 `~/.local/share/lcharness/overlays/<repo-hash>/`
  - 当前仓内只存放控制面脚本，不存放注册状态

## 目录说明

| 脚本 | 作用 | 调用方式 |
|------|------|---------|
| [`lc-repo-registry.sh`](./lc-repo-registry.sh) | Repo Registry 读写（add / remove / list / get / update / exists） | `bash lc-repo-registry.sh add <path> --profile <name>` |
| [`lc-attach.sh`](./lc-attach.sh) | attach + inject + validate 一键闭环入口 | `bash lc-attach.sh <repo-path> --profile <name>` |
| [`lc-status.sh`](./lc-status.sh) | 状态查询 + 健康检查 | `bash lc-status.sh [repo-id]` |
| [`lc-inject.sh`](./lc-inject.sh) | Overlay 注入（创建目录结构 + 标记文件） | `bash lc-inject.sh <repo-id>` |
| [`lc-reconcile.sh`](./lc-reconcile.sh) | Stale/Broken 修复 | `bash lc-reconcile.sh <repo-id>` |
| [`lc-detach.sh`](./lc-detach.sh) | 解注入 + registry 清理 | `bash lc-detach.sh <repo-id>` |

## 状态模型

```
detached → attached → injected → healthy ↔ stale ↔ broken → detached
```

| 状态 | 判定条件 |
|------|---------|
| detached | registry 中无记录 |
| attached | registry 有记录，但 overlay 目录不存在 |
| injected | overlay 目录 + `.lcharness-overlay` 标记文件存在 |
| healthy | injected + 标记文件字段与 registry 一致 |
| stale | 标记文件字段与 registry 不一致（profile/version 变更） |
| broken | 标记文件损坏 / 关键子目录缺失 / 权限错误 |

## 使用方式

```bash
# attach 一个新 repo
bash lc-attach.sh /path/to/repo --profile <profile-name>

# 查看所有 repo 状态
bash lc-status.sh

# 查看单个 repo 状态
bash lc-status.sh <repo-id>

# 手动 reconcile
bash lc-reconcile.sh <repo-id>

# 解注入
bash lc-detach.sh <repo-id>
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-07-02-lcharness-framework-design.md` | LcHarness 总体设计基线 |
| 层映射 | `../config/lcharness-layer-map.yaml` | Phase 1 层次映射 |
| 架构参考 | `../reference/lcharness-architecture.md` | 当前工程到 LcHarness 映射 |
```

- [ ] **Step 2: Update harness README**

In `engineering/harness/README.md`:
- Add `| [control-plane/](./control-plane/) | LcHarness 中央控制面：Repo Registry + 生命周期管理 | [control-plane/README.md](./control-plane/README.md) |` to the directory table
- Add `| 注册 repo 到 LcHarness 控制面 | [control-plane/README.md](./control-plane/README.md) |` to the fast-nav table

- [ ] **Step 3: Update layer-map**

In `engineering/harness/config/lcharness-layer-map.yaml`, add one entry:

```yaml
  - path: engineering/harness/control-plane/
    kind: directory
    layer: control-plane
    component: central-control-plane
    target: lcharness/control-plane
    rationale: central control plane for repo registry, attach/inject/validate/detach/reconcile lifecycle
```

- [ ] **Step 4: Commit**

```bash
git add engineering/harness/control-plane/README.md engineering/harness/README.md engineering/harness/config/lcharness-layer-map.yaml
git commit -m "feat(control-plane): 新增控制面目录结构、README与层映射"
```

---

## Task 2: Implement lc-repo-registry.sh

**Files:**
- Create: `engineering/harness/control-plane/lc-repo-registry.sh`

- [ ] **Step 1: Write the registry script**

Create `engineering/harness/control-plane/lc-repo-registry.sh` with the following behavior:

**Registry file location:** `~/.local/share/lcharness/registry.yaml`
**Lock file:** `~/.local/share/lcharness/registry.yaml.lock`

**Subcommands and expected behavior:**

| Subcommand | Args | Behavior |
|-----------|------|----------|
| `add` | `<path> --profile <name>` | Generate repo-id from `md5sum <path> \| cut -c1-12`. Create registry if absent. Validate no duplicate path. Write entry with state=attached, attached_at=now. Return id. |
| `remove` | `<id>` | Remove entry from registry. Do NOT clean overlay. |
| `list` | — | Table output: `id \t path \t profile \t state` |
| `get` | `<id>` | Output entry as key=value lines. Exit 1 if not found. |
| `update` | `<id> <field> <value>` | Only allow: `state`, `last_reconcile`, `health.result`, `health.last_check`. Validate enum for `state`. Write lock + backup. |
| `exists` | `<id>` | Exit 0 if found, 1 if not. |

**Registry YAML format:**

```yaml
version: 1
repos:
  - id: a1b2c3d4e5f6
    path: /mnt/d/Code/Github/AndroidSystemEnhance
    profile: android-system-enhance
    overlay_root: ~/.local/share/lcharness/overlays/a1b2c3d4e5f6/
    state: attached
    attached_at: "2026-07-02T12:00:00Z"
    last_reconcile: ""
    health:
      last_check: ""
      result: ""
```

**Implementation notes:**

1. Use `python3` with `yaml` module for all YAML read/write (same as existing validators)
2. Write lock: use `flock` if available, fallback to `mkdir` atomic lock. Lock timeout 5s.
3. Backup: before each write, copy `registry.yaml` to `registry.yaml.bak`
4. Validate after write: auto-run structure check
5. Use existing `harness_bootstrap.sh` for observability: `harness_init`, `log_*`, `step_begin/end`, `harness_exit`
6. Subcommand dispatch via `case "${1:-}" in`

**Subcommand skeleton (inline in the plan for the implementer):**

```bash
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"

harness_init "lc-repo-registry"

REGISTRY_DIR="${HOME}/.local/share/lcharness"
REGISTRY_FILE="${REGISTRY_DIR}/registry.yaml"
LOCK_FILE="${REGISTRY_DIR}/registry.yaml.lock"

# Ensure registry directory exists
mkdir -p "$REGISTRY_DIR"

# --- Helper: acquire lock ---
acquire_lock() { ... }
release_lock() { ... }

# --- Helper: read registry ---
read_registry() { python3 -c "... load yaml from REGISTRY_FILE ..." }

# --- Helper: write registry ---
write_registry() { python3 -c "... dump yaml to REGISTRY_FILE with backup ..." }

# --- Subcommands ---
cmd_add() { ... }
cmd_remove() { ... }
cmd_list() { ... }
cmd_get() { ... }
cmd_update() { ... }
cmd_exists() { ... }

case "${1:-}" in
    add) shift; cmd_add "$@" ;;
    remove) shift; cmd_remove "$@" ;;
    list) cmd_list ;;
    get) shift; cmd_get "$@" ;;
    update) shift; cmd_update "$@" ;;
    exists) shift; cmd_exists "$@" ;;
    *) echo "Usage: ..."; harness_exit 1 ;;
esac
```

**Validation rules embedded in python3 helper:**

- `version` must be positive int
- `repos` must be list (or absent for empty registry)
- Each entry must have: `id`, `path`, `profile`, `overlay_root`, `state`
- `state` must be one of: `detached`, `attached`, `injected`, `healthy`, `stale`, `broken`
- `id` must be unique across entries

- [ ] **Step 2: Run initial validation**

```bash
python3 -c "import yaml; print('yaml available')"
bash engineering/harness/scripts/validate_harness_scripts.sh
```

Expected: `yaml available` + scripts validator PASS

- [ ] **Step 3: Create an empty registry directory and smoke-test**

```bash
mkdir -p ~/.local/share/lcharness
bash engineering/harness/control-plane/lc-repo-registry.sh list
```

Expected: empty table (no repos)

- [ ] **Step 4: Commit**

```bash
git add engineering/harness/control-plane/lc-repo-registry.sh
git commit -m "feat(control-plane): 实现 Repo Registry 读写脚本 lc-repo-registry.sh"
```

---

## Task 3: Implement lc-attach.sh and lc-inject.sh

**Files:**
- Create: `engineering/harness/control-plane/lc-attach.sh`
- Create: `engineering/harness/control-plane/lc-inject.sh`

- [ ] **Step 1: Implement lc-inject.sh**

**Behavior:**
1. Accept `<repo-id>` as arg
2. Call `lc-repo-registry.sh get <id>` to get path + overlay_root
3. Create `overlay_root` directory if absent
4. Write `.lcharness-overlay` marker file (JSON):
   ```json
   {
     "version": "1",
     "repo_id": "<id>",
     "profile": "<profile>",
     "attached_at": "<timestamp>",
     "lcharness_version": "1.0"
   }
   ```
5. Write `.gitignore` with content `*`
6. Create `capabilities/` dir with `.placeholder` file
7. Update registry state to `injected` via `lc-repo-registry.sh update <id> state injected`
8. Output overlay path

**Error handling:**
- If `<id>` not in registry → exit 1 with error
- If overlay already exists with valid marker → log "already injected, skipping" (idempotent)
- If overlay exists but marker corrupted → log "overlay exists but marker invalid, re-injecting"

- [ ] **Step 2: Implement lc-attach.sh**

**Behavior:**
1. Accept `<repo-path> --profile <name>` as args
2. Validate `<repo-path>` is a readable directory
3. Call `lc-repo-registry.sh add <path> --profile <name>` to register
4. Capture returned `id`
5. Call `lc-inject.sh <id>` to create overlay
6. Call `lc-validate.sh <id>` (or inline check) to verify healthy
7. Output summary: `Attached: <id> -> <path> [healthy]`

**Error handling:**
- If path doesn't exist → exit 1
- If path already registered → exit 1 with "already attached"
- If inject fails → clean up registry entry (rollback), exit 1
- If validate fails → log "attached but not healthy", do NOT rollback (allow manual reconcile)

- [ ] **Step 3: Smoke test**

```bash
bash engineering/harness/control-plane/lc-attach.sh /mnt/d/Code/Github/AndroidSystemEnhance --profile android-system-enhance
bash engineering/harness/control-plane/lc-repo-registry.sh list
```

Expected:
- `lc-attach.sh` exits 0, prints id + healthy
- `registry.yaml` contains the new entry with state=healthy
- `~/.local/share/lcharness/overlays/<hash>/` has `.lcharness-overlay`, `.gitignore`, `capabilities/.placeholder`

- [ ] **Step 4: Clean up test data**

```bash
bash engineering/harness/control-plane/lc-detach.sh <id>
```

(Continue to next task even if detach not yet implemented — manual cleanup: `rm -rf ~/.local/share/lcharness/`)

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/control-plane/lc-attach.sh engineering/harness/control-plane/lc-inject.sh
git commit -m "feat(control-plane): 实现 attach 与 inject 脚本"
```

---

## Task 4: Implement lc-validate.sh and lc-status.sh

**Files:**
- Create: `engineering/harness/control-plane/lc-validate.sh`
- Create: `engineering/harness/control-plane/lc-status.sh`

- [ ] **Step 1: Implement lc-validate.sh**

**Input:** `<repo-id>`

**Logic function `determine_state(id)`:**
1. `get` entry from registry → if not found, state=detached
2. If registry entry exists but `overlay_root` dir missing → state=attached
3. If overlay dir exists but `.lcharness-overlay` missing → state=broken (reason: marker missing)
4. Read `.lcharness-overlay` JSON, compare fields with registry:
   - `profile` mismatch → state=stale (reason: profile changed)
   - `version` mismatch → state=stale (reason: overlay version changed)
   - `repo_id` mismatch → state=broken (reason: marker repo_id mismatch)
5. If marker valid + all fields match → state=healthy
6. Check `capabilities/` dir exists → if missing, state=broken (reason: capabilities dir missing)

**Output:** exit code + stdout:
```
STATUS=<state>
REASON=<reason>
```

Exit code: 0 for healthy, 1 for stale/broken/detached/attached

- [ ] **Step 2: Implement lc-status.sh**

**Input:** optional `<repo-id>`

**Without id:** `list` all repos from registry, for each call `lc-validate.sh`, output table:

```
ID             PATH                                      PROFILE                  STATE      LAST_CHECK
a1b2c3d4e5f6   /mnt/.../AndroidSystemEnhance              android-system-enhance   healthy    2026-07-02T12:00:00Z
```

If `lc-validate.sh` call fails, show state `unknown` with error reason.

**With id:** output single entry detail in verbose format:
```
Repo ID:       a1b2c3d4e5f6
Path:          /mnt/.../AndroidSystemEnhance
Profile:       android-system-enhance
State:         healthy
Attached:      2026-07-02T12:00:00Z
Last Check:    2026-07-02T12:00:00Z
Overlay:       ~/.local/share/lcharness/overlays/a1b2c3d4e5f6/
```

- [ ] **Step 3: Test with the previously attached repo**

```bash
# Re-attach (if cleaned after Task 3)
bash engineering/harness/control-plane/lc-attach.sh /mnt/d/Code/Github/AndroidSystemEnhance --profile android-system-enhance

# Validate
bash engineering/harness/control-plane/lc-validate.sh <id>
echo $?  # should be 0

# Status
bash engineering/harness/control-plane/lc-status.sh
bash engineering/harness/control-plane/lc-status.sh <id>
```

- [ ] **Step 4: Test stale detection**

```bash
# Manually modify marker file to simulate profile change
sed -i 's/android-system-enhance/test-profile/' ~/.local/share/lcharness/overlays/<hash>/.lcharness-overlay

bash engineering/harness/control-plane/lc-validate.sh <id>
echo $?  # should be 1
# stdout should include STATUE=stale, REASON=profile changed

# Restore marker
sed -i 's/test-profile/android-system-enhance/' ~/.local/share/lcharness/overlays/<hash>/.lcharness-overlay
```

- [ ] **Step 5: Test broken detection**

```bash
# Remove capabilities dir to simulate broken state
rm -rf ~/.local/share/lcharness/overlays/<hash>/capabilities

bash engineering/harness/control-plane/lc-validate.sh <id>
echo $?  # should be 1
# stdout should include STATUE=broken, REASON=capabilities dir missing

# Restore
mkdir -p ~/.local/share/lcharness/overlays/<hash>/capabilities
```

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/control-plane/lc-validate.sh engineering/harness/control-plane/lc-status.sh
git commit -m "feat(control-plane): 实现 validate 与 status 脚本（含 stale/broken 检测）"
```

---

## Task 5: Implement lc-reconcile.sh and lc-detach.sh

**Files:**
- Create: `engineering/harness/control-plane/lc-reconcile.sh`
- Create: `engineering/harness/control-plane/lc-detach.sh`

- [ ] **Step 1: Implement lc-reconcile.sh**

**Input:** `<repo-id>`

**Logic:**
1. `get` entry from registry → if not found, exit 1 with "not registered"
2. Run `lc-validate.sh <id>` → capture current state
3. If healthy → log "already healthy, nothing to reconcile", exit 0
4. If stale → reconstruct overlay structure (re-run inject logic), update marker file from truth
5. If broken → try to determine which part is broken:
   - Missing overlay dir → run inject
   - Missing marker file → recreate from registry
   - Missing capabilities dir → recreate
   - Permissions → log error, exit 1 (manual fix required)
6. Run `lc-validate.sh <id>` again to confirm
7. Update `last_reconcile` in registry via `lc-repo-registry.sh update`
8. Exit 0 if healthy, 1 if still broken

- [ ] **Step 2: Implement lc-detach.sh**

**Input:** `<repo-id>`

**Logic:**
1. `get` entry from registry → if not found, exit 1 with "not registered"
2. Print warning: "Detaching <id> (<path>) will remove overlay at <overlay_root>. Continue? [y/N]"
3. Read confirmation from stdin
4. If not 'y' or 'Y', exit 0 with "cancelled"
5. Remove overlay directory: `rm -rf <overlay_root>`
6. Verify removal: `ls <overlay_root>` should fail; list any remaining files
7. Remove registry entry: `lc-repo-registry.sh remove <id>`
8. Output "Detached and cleaned: <id>"

- [ ] **Step 3: End-to-end test**

```bash
# Full lifecycle test
ID=$(bash engineering/harness/control-plane/lc-attach.sh /mnt/d/Code/Github/AndroidSystemEnhance --profile android-system-enhance | grep -oP '(?<=id: )[a-f0-9]+')

bash engineering/harness/control-plane/lc-status.sh $ID  # should be healthy

# Simulate stale
echo '{"version":"2"}' > ~/.local/share/lcharness/overlays/$ID/.lcharness-overlay
bash engineering/harness/control-plane/lc-reconcile.sh $ID
bash engineering/harness/control-plane/lc-status.sh $ID  # should be healthy again

# Detach (with confirmation - pipe 'y')
echo 'y' | bash engineering/harness/control-plane/lc-detach.sh $ID
bash engineering/harness/control-plane/lc-status.sh $ID  # should not appear
```

- [ ] **Step 4: Commit**

```bash
git add engineering/harness/control-plane/lc-reconcile.sh engineering/harness/control-plane/lc-detach.sh
git commit -m "feat(control-plane): 实现 reconcile 与 detach 脚本"
```

---

## Task 6: Tests for control-plane scripts

**Files:**
- Create: `engineering/harness/tests/test_lcharness_control_plane.sh`
- Modify: `engineering/harness/tests/README.md`
- Modify: `engineering/harness/tests/run_all_tests.sh`

- [ ] **Step 1: Write the combined test script**

Create `engineering/harness/tests/test_lcharness_control_plane.sh` with the following test functions:

```
test_registry_add_then_get()
test_registry_remove()
test_registry_list()
test_registry_update()
test_registry_exists()
test_registry_duplicate_path_rejected()
test_registry_invalid_state_rejected()

test_attach_new_repo()
test_attach_duplicate_rejected()
test_attach_invalid_path_rejected()

test_inject_creates_overlay()
test_inject_idempotent()
test_inject_marker_content()

test_validate_healthy()
test_validate_stale()
test_validate_broken()
test_validate_not_registered()

test_reconcile_healthy_nop()
test_reconcile_stale_fix()
test_reconcile_broken_missing_overlay()
test_reconcile_broken_missing_marker()

test_detach_cleans_overlay()
test_detach_cancelled()
test_detach_not_registered()
```

**Test sandbox:** Use a temporary directory as a fake "repo" for attach/detach tests:

```bash
TMP_REPO="$(mktemp -d)"
mkdir -p "$TMP_REPO/.git"  # minimal fake git repo
```

**Important:** All tests must clean up after themselves. Use `trap` for cleanup.

**Test count:** 22 test functions expected.

- [ ] **Step 2: Register in test README**

In `engineering/harness/tests/README.md`:
- Add file row:
  ```markdown
  | [`test_lcharness_control_plane.sh`](./test_lcharness_control_plane.sh) | LcHarness 控制面测试（registry/attach/inject/validate/reconcile/detach） | `bash test_lcharness_control_plane.sh` |
  ```
- Add regression matrix row:
  ```markdown
  | `test_lcharness_control_plane.sh` | control-plane 脚本集 | 22 | — | ✅ |
  ```

- [ ] **Step 3: Add to run_all_tests.sh**

In `engineering/harness/tests/run_all_tests.sh`, add line after existing test entries:

```bash
bash "$TEST_DIR/test_lcharness_control_plane.sh" || all_pass=1
```

- [ ] **Step 4: Run tests**

```bash
bash engineering/harness/tests/test_lcharness_control_plane.sh
bash engineering/harness/tests/run_all_tests.sh
```

Expected: All 22 tests PASS, run_all_tests PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/tests/test_lcharness_control_plane.sh engineering/harness/tests/README.md engineering/harness/tests/run_all_tests.sh
git commit -m "feat(test): 新增 LcHarness 控制面全量测试（22 测试点）"
```

---

## Task 7: Update control-plane in harness scripts validator and full validation

**Files:**
- Modify: `engineering/harness/scripts/validate_harness_scripts.sh`
- Modify: `engineering/harness/scripts/validate_harness_config.sh`
- Modify: `engineering/harness/scripts/README.md`

- [ ] **Step 1: Extend scripts validator**

In `engineering/harness/scripts/validate_harness_scripts.sh`, add `control-plane/` to the scanned directory list so that the 5 new scripts are subject to bootstrap/harness_init/exit compliance checks.

Current scanning likely includes `scripts/*.sh` and `workflows/*/bin/*.sh`. Add:
```bash
control_plane_scripts=$(find "$HARNESS_DIR/control-plane" -maxdepth 1 -name '*.sh' 2>/dev/null | sort)
```

- [ ] **Step 2: Re-validate**

```bash
bash engineering/harness/scripts/validate_harness_scripts.sh
bash engineering/harness/scripts/validate_harness_config.sh
bash engineering/harness/scripts/validate_lcharness_layer_map.sh
run_all_validations.sh
```

Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add engineering/harness/scripts/validate_harness_scripts.sh
git commit -m "fix(validator): 将 control-plane/ 纳入脚本合规扫描范围"
```

---

## Task 8: Add lc as a project-level alias for easy access

**Files:**
- Modify: `AGENTS.md` (or create simple wrapper command in `.opencode/commands/`)

- [ ] **Step 1: Create convenience wrapper (or document in AGENTS.md)**

Option A: Add a note in `AGENTS.md` suggesting:
```bash
alias lc='bash engineering/harness/control-plane/lc-'
```

Option B: Create a `.opencode/commands/lc-attach.md` that maps to the actual script.

Since attaching/detaching are manual ops for the human developer (not frequent AI actions), Option A (documented alias) is sufficient. No need for a full `.opencode/command/` entry.

Add to `AGENTS.md` at the end of instructions:
```markdown
## LcHarness 控制面快捷命令

```bash
alias lc-attach='bash engineering/harness/control-plane/lc-attach.sh'
alias lc-status='bash engineering/harness/control-plane/lc-status.sh'
alias lc-detach='bash engineering/harness/control-plane/lc-detach.sh'
alias lc-validate='bash engineering/harness/control-plane/lc-validate.sh'
alias lc-reconcile='bash engineering/harness/control-plane/lc-reconcile.sh'
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "feat(control-plane): 添加 LcHarness 控制面别名快捷命令"
```

---

## Final verification

- [ ] **Step 1: Full lifecycle end-to-end test**

```bash
# 1. Clean start
rm -rf ~/.local/share/lcharness/

# 2. Attach current repo
ID=$(bash engineering/harness/control-plane/lc-attach.sh /mnt/d/Code/Github/AndroidSystemEnhance --profile android-system-enhance | grep -oP '(?<=id: )[a-f0-9]+')
echo "ID: $ID"

# 3. Status should show healthy
bash engineering/harness/control-plane/lc-status.sh "$ID" | grep -q "healthy" && echo "PASS: healthy"

# 4. Validate should exit 0
bash engineering/harness/control-plane/lc-validate.sh "$ID" && echo "PASS: validate OK"

# 5. Simulate stale
echo '{"version":"2"}' > ~/.local/share/lcharness/overlays/$ID/.lcharness-overlay
bash engineering/harness/control-plane/lc-validate.sh "$ID" || echo "PASS: stale detected"

# 6. Reconcile
bash engineering/harness/control-plane/lc-reconcile.sh "$ID"
bash engineering/harness/control-plane/lc-validate.sh "$ID" && echo "PASS: reconciled to healthy"

# 7. Detach
echo 'y' | bash engineering/harness/control-plane/lc-detach.sh "$ID"
bash engineering/harness/control-plane/lc-repo-registry.sh get "$ID" 2>&1 || echo "PASS: detached"

echo "=== E2E PASS ==="
```

- [ ] **Step 2: Run full test suite**

```bash
bash engineering/harness/tests/test_lcharness_control_plane.sh
bash engineering/harness/tests/run_all_tests.sh
bash engineering/harness/scripts/run_all_validations.sh
```

Expected: All PASS

- [ ] **Step 3: Review final diff**

```bash
git diff --stat HEAD~8..HEAD
```

Expected: Changes limited to:
- `engineering/harness/control-plane/` (6 files: 5 scripts + 1 README)
- `engineering/harness/tests/test_lcharness_control_plane.sh`
- `engineering/harness/tests/README.md` (update)
- `engineering/harness/tests/run_all_tests.sh` (update)
- `engineering/harness/README.md` (nav update)
- `engineering/harness/config/lcharness-layer-map.yaml` (new entry)
- `engineering/harness/scripts/validate_harness_scripts.sh` (scan scope)
- `AGENTS.md` (aliases)

- [ ] **Step 4: Final commit**

```bash
git add engineering/harness/control-plane/ engineering/harness/tests/test_lcharness_control_plane.sh engineering/harness/tests/README.md engineering/harness/tests/run_all_tests.sh engineering/harness/README.md engineering/harness/config/lcharness-layer-map.yaml engineering/harness/scripts/validate_harness_scripts.sh AGENTS.md
git commit -m "feat(lcharness): 建立 Phase 2 中央控制面与 Repo Registry"
```

---

## Notes for the executor

1. All scripts must source `harness_bootstrap.sh` and use `harness_init` / `log_*` / `harness_exit`. Follow existing scripts (e.g., `validate_lcharness_layer_map.sh`) as reference for observability patterns.
2. Registry YAML must use python3 `yaml` module — consistent with existing validators. No external dependencies beyond what the project already uses.
3. The lock file (`registry.yaml.lock`) uses `flock` when available on Linux; fallback to `mkdir` atomic lock on systems without `flock` (e.g., some CI environments).
4. All overlay operations target `~/.local/share/lcharness/` — never write to any tracked directory inside the repo. This is the zero-contamination guarantee.
5. The attach script generates repo-id from `md5sum` of the path. This is deterministic: same path always produces same id. No need for UUID generation.
6. Do NOT implement Phase 3 overlay capability projection (symlinks, profile resolution, pack-specific files) during Phase 2. Phase 2 overlay is purely structural.
7. After detach, verify no leftover files. Use `ls` + grep to confirm the overlay root is completely removed; report any remnant files to the user.
8. `lc-attach.sh` is the primary user-facing entry point. All others are advanced/troubleshooting tools. Ensure attach has good error messages.
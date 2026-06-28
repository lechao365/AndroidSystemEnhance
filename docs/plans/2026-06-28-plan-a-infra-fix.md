# Plan A: 基础设施修复 + 基线验证（Task 1-4）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 loop engineering v2 在 lcview 模块场景下的 3 个 gap（G1 vendor/lechao 非 git / G2 sync 污染 .git / G3 worktree ws_root 错误），并建立 lcview suite 基线。

**Spec:** `docs/specs/2026-06-28-lcview-fault-injection-loop-validation-design.md` §3.1

**Test Command:**
```bash
PYTHONPATH="engineering/loop/controller/python:engineering/loop/contracts/python:engineering/loop/core/python:engineering/loop/deploy/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python" \
  python3 -m pytest engineering/loop/ --tb=short -v
```

---

## Task 1: vendor/lechao 本地 git 仓库初始化（G1 修复）

**目标：** 给 `~/workspace/aosp/vendor/lechao` 创建本地 git 仓库，为 loop runtime worktree 隔离提供基础。

**Files:**
- Create: `~/workspace/aosp/vendor/lechao/.git/`（git init）
- Create: `~/workspace/aosp/vendor/lechao/.gitignore`

- [ ] **Step 1: 确认当前无 git 状态**

```bash
cd ~/workspace/aosp/vendor/lechao && git rev-parse --is-inside-work-tree 2>&1
# 预期：fatal: not a git repository
```

- [ ] **Step 2: 创建本地 git 仓库**

```bash
cd ~/workspace/aosp/vendor/lechao
git init
git config user.email "lechao@local"
git config user.name "lechao"
git config commit.gpgsign false
```

- [ ] **Step 3: 创建 .gitignore**

写入 `~/workspace/aosp/vendor/lechao/.gitignore`：
```
*.o
*.ko
*.cmd
*.symvers
out/
prebuilts/
```

- [ ] **Step 4: 全量提交 baseline**

```bash
cd ~/workspace/aosp/vendor/lechao
git add -A
git commit -m "vendor/lechao baseline (lcview + lciod + include)

Local-only git repo for loop engineering worktree isolation.
Not tracked to any remote."
git log --oneline -1
```

- [ ] **Step 5: 验证 git 状态干净**

```bash
cd ~/workspace/aosp/vendor/lechao && git status --short
# 预期：无输出（working tree clean）
git rev-parse --is-inside-work-tree
# 预期：true
```

- [ ] **Step 6: 验证 loop runtime 可识别为 git 仓库**

```bash
LE_PYTHONPATH="/mnt/d/Code/Github/AndroidSystemEnhance/engineering/loop/controller/python"
python3 -c "
import sys; sys.path.insert(0, '$LE_PYTHONPATH')
from loop_controller.workspace_isolation import _is_git_repo
import os
print('is_git:', _is_git_repo(os.path.expanduser('~/workspace/aosp/vendor/lechao')))
"
# 预期：is_git: True
```

---

## Task 2: sync/revert 脚本排除 .git 目录（G2 修复）

**目标：** 修改 `HARNESS_EXCLUDE_*` 常量，防止 lc-sync-code-to-patchs 把 vendor/lechao/.git 同步到 patchs。

**Files:**
- Modify: `engineering/harness/lib/shell/harness_observability.sh:50-52`

> **规则遵循：** 已加载 OBS-001。本 Task 只改常量定义，不改 step/exit 逻辑。

- [ ] **Step 1: 读取当前排除规则确认行号**

```bash
grep -n "HARNESS_EXCLUDE" /mnt/d/Code/Github/AndroidSystemEnhance/engineering/harness/lib/shell/harness_observability.sh
```

- [ ] **Step 2: 修改 HARNESS_EXCLUDE_RE（追加 |^\\.git/）**

将 line 50 的 `HARNESS_EXCLUDE_RE` 追加 `|^\.git/`：
```bash
# 修改前：
HARNESS_EXCLUDE_RE='\.o$|\.ko$|\.cmd$|\.symvers$|^Image$|\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|\.prebuilt/|\.prev/'
# 修改后：
HARNESS_EXCLUDE_RE='\.o$|\.ko$|\.cmd$|\.symvers$|^Image$|\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|\.prebuilt/|\.prev/|^\.git/'
```

- [ ] **Step 3: 修改 HARNESS_EXCLUDE_DIR_RE（追加 |^\\.git$）**

将 line 52 的 `HARNESS_EXCLUDE_DIR_RE` 追加 `|^\.git$`：
```bash
# 修改前：
HARNESS_EXCLUDE_DIR_RE='^(out|prebuilts)$'
# 修改后：
HARNESS_EXCLUDE_DIR_RE='^(out|prebuilts)$|^\.git$'
```

- [ ] **Step 4: 验证排除规则生效**

```bash
cd /mnt/d/Code/Github/AndroidSystemEnhance
source engineering/harness/lib/shell/harness_bootstrap.sh
harness_init "test-exclude"
echo ".git/HEAD" | grep -qE "$HARNESS_EXCLUDE_RE" && echo "RE_EXCLUDED" || echo "RE_NOT_EXCLUDED"
echo ".git/objects/ab/cdef123" | grep -qE "$HARNESS_EXCLUDE_RE" && echo "RE2_EXCLUDED" || echo "RE2_NOT_EXCLUDED"
echo ".git" | grep -qE "$HARNESS_EXCLUDE_DIR_RE" && echo "DIR_EXCLUDED" || echo "DIR_NOT_EXCLUDED"
harness_exit 0
# 预期：三个都输出 *_EXCLUDED
```

- [ ] **Step 5: dry-run 验证 sync 不含 .git**

```bash
bash engineering/harness/workflows/lc-sync-code-to-patchs/sync_code_to_patchs.sh --check-only 2>&1 | grep -i "\.git" | head -5 || echo "NO_GIT_SYNCED"
# 预期：NO_GIT_SYNCED
```

- [ ] **Step 6: Commit**

```bash
cd /mnt/d/Code/Github/AndroidSystemEnhance
git add engineering/harness/lib/shell/harness_observability.sh
git commit -m "fix(harness): exclude .git from sync/revert non-repo dir scanning

vendor/lechao is now a local git repo for loop worktree isolation.
Without this fix, lc-sync-code-to-patchs would copy .git/ contents
into patchs/, polluting the archive."
```

---

## Task 3: loop runtime worktree ws_root 定位修复（G3 修复）

**目标：** 让 patch 相关 git 操作定位到 vendor/lechao（git 仓库），而非 ~/workspace/aosp（非 git）。

**Files:**
- Modify: `engineering/harness/config/harness-paths.conf`
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`
- Modify: `engineering/loop/controller/python/loop_controller/runtime/nodes.py`
- Modify: `engineering/loop/scripts/le.sh`（如需 export）

> **规则遵循：** 已加载 PATH-001。新增路径 KEY 到 paths.conf（单一事实源）。

- [ ] **Step 1: 新增 paths.conf KEY**

在 `engineering/harness/config/harness-paths.conf` 的「环境可覆盖路径」段末尾（line 45 后）追加：
```bash
# LE_PATCH_GIT_ROOT: loop runtime 补丁隔离的 git 仓库根（vendor/lechao 本地 git）
ENV_LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/vendor/lechao"
```

- [ ] **Step 2: 修改 engine.py 的 ws_root 定位**

定位 engine.py 中 APPLY_PATCH 节点读取 ws_root 的位置（搜索 `AOSP_ROOT`）：
```bash
grep -n "AOSP_ROOT\|ws_root\|workspace_root" engineering/loop/controller/python/loop_controller/runtime/engine.py
```

将所有 `os.environ.get("AOSP_ROOT", ...)` 改为优先读 `LE_PATCH_GIT_ROOT`：
```python
# 优先用 LE_PATCH_GIT_ROOT（vendor/lechao git 仓库），支持 worktree 隔离；
# 回退到 AOSP_ROOT（兼容旧环境）
ws_root = os.environ.get("LE_PATCH_GIT_ROOT") or os.environ.get(
    "AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
```

- [ ] **Step 3: 修改 nodes.py 的 _workspace_root**

`nodes.py:23-27` 的 `_workspace_root` 函数，同样优先读 `LE_PATCH_GIT_ROOT`：
```python
def _workspace_root(workspace_root: str = "") -> str:
    """解析 workspace 根路径，缺省回退到 LE_PATCH_GIT_ROOT → AOSP_ROOT。"""
    return workspace_root or os.environ.get("LE_PATCH_GIT_ROOT") or os.environ.get(
        "AOSP_ROOT", os.path.expanduser("~/workspace/aosp")
    )
```

- [ ] **Step 4: 修改 node_apply_patch 的 workspace_path prefix strip（worktree 模式）**

`nodes.py` 的 `node_apply_patch` 函数中，当 `worktree_handle` 非空时，apply_root 是 worktree_path（vendor/lechao 快照根），但 FileChange.workspace_path 含 `vendor/lechao/` 前缀。需要 strip：

在 `apply_root = worktree_handle.worktree_path if worktree_handle else ws_root` 之后，对 worktree 模式做 path strip。具体实现：在 `apply_file_changes` 调用前，对 changes 的 workspace_path 做 prefix strip。

新增辅助函数：
```python
_LE_GIT_ROOT_PREFIX = "vendor/lechao/"

def _strip_le_prefix(path: str) -> str:
    """worktree 模式下，workspace_path 含 vendor/lechao/ 前缀，需 strip 到 git 仓库根的相对路径。"""
    if path.startswith(_LE_GIT_ROOT_PREFIX):
        return path[len(_LE_GIT_ROOT_PREFIX):]
    return path
```

在 `node_apply_patch` 中 worktree 模式下，对 changes 做 strip：
```python
if worktree_handle:
    # worktree 是 vendor/lechao 的快照，strip 掉 vendor/lechao/ 前缀
    stripped_changes = []
    for fc in changes:
        stripped = FileChange(
            workspace_path=_strip_le_prefix(fc.workspace_path),
            change_type=fc.change_type,
            new_content=fc.new_content,
            old_marker=fc.old_marker,
            line_range=fc.line_range,
            diff=fc.diff,
        )
        stripped_changes.append(stripped)
    changes = stripped_changes
```

同时 `result.applied_files` 需要保持原始的 workspace_path（带前缀），用于 session 记录和白名单校验（白名单用带前缀的路径）。

- [ ] **Step 5: 修改 le.sh export 环境变量**

确认 le.sh 在 harness_init 后 export：
```bash
grep -n "export.*AOSP_ROOT\|export.*LE_PATCH" engineering/loop/scripts/le.sh
```

若未 export，在 le.sh 的环境初始化段追加：
```bash
export LE_PATCH_GIT_ROOT="${LE_PATCH_GIT_ROOT:-$HOME/workspace/aosp/vendor/lechao}"
```

- [ ] **Step 6: 验证 worktree 创建/删除**

```bash
export LE_PATCH_GIT_ROOT="$HOME/workspace/aosp/vendor/lechao"
python3 -c "
import sys, os
sys.path.insert(0, '/mnt/d/Code/Github/AndroidSystemEnhance/engineering/loop/controller/python')
from loop_controller.workspace_isolation import _is_git_repo, create_patch_worktree, remove_patch_worktree
ws = os.environ['LE_PATCH_GIT_ROOT']
assert _is_git_repo(ws), 'not a git repo'
h = create_patch_worktree(ws, 'test-plan-a', 0)
print(f'created: {h.created} path={h.worktree_path}')
assert os.path.isdir(h.worktree_path), 'worktree dir missing'
assert remove_patch_worktree(h), 'remove failed'
print('worktree create + remove: OK')
"
```

- [ ] **Step 7: 单元回归**

```bash
PYTHONPATH="engineering/loop/controller/python:engineering/loop/contracts/python:engineering/loop/core/python:engineering/loop/deploy/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -v -k "workspace or patch or engine or node" --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add engineering/harness/config/harness-paths.conf \
        engineering/loop/controller/python/loop_controller/runtime/engine.py \
        engineering/loop/controller/python/loop_controller/runtime/nodes.py \
        engineering/loop/scripts/le.sh
git commit -m "fix(loop): worktree ws_root targets vendor/lechao git repo

~/workspace/aosp is repo-managed (not git), so worktree creation always
failed. Add LE_PATCH_GIT_ROOT env (default: vendor/lechao local git)
for patch isolation. Includes workspace_path prefix strip for worktree
mode. Falls back to AOSP_ROOT for backward compatibility."
```

---

## Task 4: 基线验证 — lcview suite 当前状态 PASS

**目标：** 确认 lcview common suite 在当前设备上全部 PASS，建立故障注入前的基线。

**Files:** 无代码改动。

- [ ] **Step 1: 确认设备可达（串口 + adb）**

```bash
python3 engineering/loop/scripts/rp5_serial_helper.py prop sys.boot_completed --host 127.0.0.1 --port 9700
# 预期：1

DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)
echo "DEV_IP=$DEV_IP"
adb connect $DEV_IP:5555
adb devices
```

- [ ] **Step 2: 跑 lcview common suite**

```bash
ARTIFACTS=engineering/output/runs/lcview-baseline-$(date +%Y%m%d%H%M%S)
DEV_IP=$(python3 engineering/loop/scripts/rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700)
bash engineering/loop/scripts/le.sh run \
  --suite engineering/loop/cases/features/lcview/common.yaml \
  --adb-endpoint $DEV_IP:5555 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/adb.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir $ARTIFACTS
```

- [ ] **Step 3: 检查全 PASS**

```bash
python3 -c "
import json
b = json.load(open('$ARTIFACTS/evidence_bundle.json'))
fails = [c for c in b['cases'] if c['status'] != 'pass']
print(f'total={len(b[\"cases\"])}, pass={len(b[\"cases\"])-len(fails)}, fail={len(fails)}')
for f in fails: print(f'  FAIL: {f[\"id\"]} - {f.get(\"failure_reason\",\"\")}')
"
# 预期：fail=0
```

- [ ] **Step 4: 若有 FAIL 先修复设备状态**

若存在 FAIL，记录失败用例，检查设备 service 状态（`adb shell getprop init.svc.lechao_lcview`），确保基线 PASS 后再进入故障注入阶段。

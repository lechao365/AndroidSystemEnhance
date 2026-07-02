# LcHarness Phase 1 Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the current `engineering/` tree into a Phase 1 `LcHarness` architecture baseline by making `core / packs / profiles / adapters / control-plane` explicit in docs, machine-readable mapping, and validation entrypoints without yet extracting a standalone repo or implementing repo injection.

**Architecture:** Phase 1 is a repository-internal productization pass. It keeps all existing code in place, but adds a formal architecture reference, a machine-readable layer map, and validators/tests that lock in the intended layering. `engineering/harness` remains the current public engineering substrate, while `engineering/loop` is reclassified as a future solution pack rather than part of core.

**Tech Stack:** Markdown, YAML, Bash, existing harness validator/test framework

---

## Scope and success criteria

### In scope
- Define the future `LcHarness` layering model in project docs.
- Create a machine-readable `lcharness-layer-map.yaml` that maps current directories and entrypoints to future layers.
- Add validators/tests so the layer map becomes a checked contract rather than an informal note.
- Update the main READMEs so contributors can navigate from current `engineering/` layout to the future `LcHarness` architecture.

### Out of scope
- No standalone `LcHarness` repo extraction yet.
- No `attach / inject / reconcile / detach` runtime.
- No business repo overlay implementation.
- No AndroidSystemEnhance first-profile runtime integration.
- No large-scale physical file moves of current implementation directories.

### Done means
- A new architecture reference exists under `engineering/harness/reference/`.
- A validated `engineering/harness/config/lcharness-layer-map.yaml` exists.
- `engineering/README.md`, `engineering/harness/README.md`, `engineering/loop/README.md`, and related READMEs point to the new architecture baseline.
- There is a dedicated validator and test for the layer map.
- `loop` is explicitly documented as a future **solution pack**, not `LcHarness` core.

---

## Task 1: Publish the LcHarness architecture reference

**Files:**
- Create: `engineering/harness/reference/lcharness-architecture.md`
- Modify: `engineering/harness/reference/README.md`
- Modify: `engineering/harness/README.md`

- [ ] **Step 1: Write the architecture reference**

Create `engineering/harness/reference/lcharness-architecture.md` with this content:

```markdown
# LcHarness Architecture Reference

> 非约束性参考文档：说明当前 `engineering/` 如何映射到未来独立 `LcHarness` 单仓全集成架构。

## 定位

- 本文描述未来 `LcHarness` 的逻辑分层：`core / packs / profiles / adapters / control-plane`
- 本文不替代 `rules/*.md`、`WORKFLOW.md` 与 `docs/specs/2026-07-02-lcharness-framework-design.md`
- 当前仓仍以 `engineering/harness`、`engineering/loop` 为实现载体；本文只定义 Phase 1 的目标映射

## 逻辑分层

### Core

业务无关、仓库无关、相对宿主无关的稳定基础设施，包括：
- rules / policy
- workflow contract
- config schema
- observability / evidence
- validator runtime
- binding / reconcile engine（未来实现）

### Packs

可插拔能力包，分为：
- platform packs
- domain packs
- solution packs

### Profiles

面向目标业务仓的装配层，只负责选择 packs、裁剪可见能力、绑定路径与 adapter 组合。

### Adapters

连接 OpenCode、shell/python/bat 与未来业务仓 overlay 的适配层。

### Control Plane

未来 `LcHarness` 中的集中控制层，负责 repo registry、attach/inject/reconcile/detach/status。

## 能力归属判定

### 可进入 core
- 不含 loop-specific 语义
- 不依赖 AndroidSystemEnhance 单仓目录假设
- 能作为跨仓公共基础设施复用
- 对外接口稳定、可被 pack/profile 消费

### 必须下沉为 pack
- 强绑定 Android/AOSP 语义
- 强绑定 patch archive / baseline / revert 语义
- 仅服务 loop runtime 或闭环诊断流程

### 属于 profile
- 只做 repo-specific 装配
- 只决定暴露哪些 skills/workflows/runtime
- 不承载框架核心逻辑

## 当前目录到未来层次的映射

| 当前路径 | 未来层次 | 说明 |
|---------|---------|------|
| `engineering/harness/config/` | core | 机器可读配置与映射层 |
| `engineering/harness/lib/` | core | 公共路径工具、bootstrap、observability |
| `engineering/harness/rules/` | core | 约束规则与 manifest 入口 |
| `engineering/harness/scripts/` | core / control-plane support | 公共校验器与未来控制面支撑脚本 |
| `engineering/harness/templates/` | core | 文档结构契约 |
| `engineering/harness/tests/` | core | harness 公共层测试 |
| `engineering/harness/workflows/` | core seed / domain-pack split pending | 当前 workflow 容器，后续按通用与领域能力拆分 |
| `engineering/loop/` | solution pack | AI 驱动验收闭环，不进入 core |
| `.opencode/commands/le.md` | adapter projection candidate | 当前业务侧入口雏形 |
| `.opencode/commands/lc-sync-code-to-patchs.md` | adapter projection candidate | 当前 workflow 暴露雏形 |

## 未来独立仓目录蓝图

```text
lcharness/
  core/
  packs/
  profiles/
  adapters/
  control-plane/
```

## Phase 1 结论

- 当前仓先做逻辑分层显式化，不做大规模物理搬迁。
- `loop engineering` 在 Phase 1 即被视为 solution pack。
- AndroidSystemEnhance 当前只作为未来首个 profile 的承载上下文，不进入 core。
```

- [ ] **Step 2: Update the reference index**

Add this row to `engineering/harness/reference/README.md` under the file table:

```markdown
| [lcharness-architecture.md](./lcharness-architecture.md) | `LcHarness` 单仓全集成架构参考：逻辑分层、能力归属、当前工程映射与未来目录蓝图 | `harness/README.md`（迁移入口）、Phase 1 计划 |
```

- [ ] **Step 3: Add navigation from harness README**

In `engineering/harness/README.md`, add one row to the “快速导航” table:

```markdown
| 查 `LcHarness` 目标架构与当前映射 | [reference/lcharness-architecture.md](./reference/lcharness-architecture.md) |
```

Also add two rows in “关联资源”:

```markdown
| 设计文档 | [docs/specs/2026-07-02-lcharness-framework-design.md](../../docs/specs/2026-07-02-lcharness-framework-design.md) | `LcHarness` 总体设计基线 |
| 参考文档 | [reference/lcharness-architecture.md](./reference/lcharness-architecture.md) | 当前工程映射到 `LcHarness` 的架构参考 |
```

- [ ] **Step 4: Run docs validation**

Run:

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

Expected: exit `0`, or only unrelated pre-existing warnings; no new warning about the new reference document or README links.

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/reference/lcharness-architecture.md engineering/harness/reference/README.md engineering/harness/README.md
git commit -m "docs(reference): 新增 LcHarness 架构参考与导航入口"
```

---

## Task 2: Create the machine-readable layer map

**Files:**
- Create: `engineering/harness/config/lcharness-layer-map.yaml`
- Modify: `engineering/harness/config/README.md`
- Modify: `engineering/README.md`
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: Create the initial layer map**

Create `engineering/harness/config/lcharness-layer-map.yaml` with this content:

```yaml
version: 1
entries:
  - path: engineering/harness/config/
    kind: directory
    layer: core
    component: config-machine-layer
    target: lcharness/core/config
    rationale: machine-readable config and mapping data shared across repositories

  - path: engineering/harness/lib/
    kind: directory
    layer: core
    component: shared-runtime-lib
    target: lcharness/core/lib
    rationale: shared bootstrap, path, and observability primitives

  - path: engineering/harness/rules/
    kind: directory
    layer: core
    component: policy-rules
    target: lcharness/core/rules
    rationale: repository-agnostic policy and manifest entrypoint

  - path: engineering/harness/scripts/
    kind: directory
    layer: core
    component: validators-and-utilities
    target: lcharness/core/scripts
    rationale: shared validators and utility scripts before control-plane split

  - path: engineering/harness/templates/
    kind: directory
    layer: core
    component: document-contracts
    target: lcharness/core/templates
    rationale: reusable document contract templates

  - path: engineering/harness/tests/
    kind: directory
    layer: core
    component: harness-tests
    target: lcharness/core/tests
    rationale: tests for reusable harness primitives and contracts

  - path: engineering/harness/workflows/
    kind: directory
    layer: pack
    pack_type: domain
    component: workflow-container
    target: lcharness/packs/android-system-enhance/workflows
    rationale: current workflow set mixes generic workflow contracts with AndroidSystemEnhance-specific semantics

  - path: engineering/harness/reference/
    kind: directory
    layer: core
    component: reference-docs
    target: lcharness/core/reference
    rationale: architecture and operational references for the reusable harness

  - path: engineering/loop/
    kind: directory
    layer: pack
    pack_type: solution
    component: loop-engineering
    target: lcharness/packs/loop-engineering
    rationale: loop is a high-level AI-driven verification loop and must not be flattened into core

  - path: .opencode/commands/le.md
    kind: file
    layer: adapter
    component: opencode-loop-projection
    target: lcharness/adapters/opencode/commands/le.md
    rationale: business-facing command projection candidate for loop capability

  - path: .opencode/commands/lc-sync-code-to-patchs.md
    kind: file
    layer: adapter
    component: opencode-workflow-projection
    target: lcharness/adapters/opencode/commands/lc-sync-code-to-patchs.md
    rationale: business-facing workflow projection candidate

  - path: AndroidSystemEnhance
    kind: virtual
    layer: profile
    component: first-profile-seed
    target: lcharness/profiles/android-system-enhance
    rationale: current repository context becomes the first target profile rather than core behavior
```

- [ ] **Step 2: Validate the YAML parses before any validator changes**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('engineering/harness/config/lcharness-layer-map.yaml', encoding='utf-8')); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Document the new config in config/README.md**

Add one row to the config file table:

```markdown
| [`lcharness-layer-map.yaml`](./lcharness-layer-map.yaml) | `LcHarness` Phase 1 层次映射：当前目录/入口到 `core / pack / profile / adapter / control-plane` 的机器可读映射 | `Phase 1` 计划、`validate_lcharness_layer_map.sh`、`validate_harness_config.sh` |
```

Add a new subsection after the existing field quick reference:

```markdown
### lcharness-layer-map.yaml

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | 整数 | 配置版本号 |
| `entries[].path` | 字符串 | 当前仓中的目录/文件/虚拟对象标识 |
| `entries[].kind` | 枚举 | `directory` / `file` / `virtual` |
| `entries[].layer` | 枚举 | `core` / `pack` / `profile` / `adapter` / `control-plane` |
| `entries[].component` | 字符串 | 稳定组件名 |
| `entries[].target` | 字符串 | 未来 `LcHarness` 中的目标位置 |
| `entries[].rationale` | 字符串 | 为什么归属该层 |
| `entries[].pack_type` | 枚举，可选 | 当 `layer=pack` 时使用：`platform` / `domain` / `solution` |
```

- [ ] **Step 4: Update top-level engineering and loop READMEs to reflect the future mapping**

In `engineering/README.md`, add one line in “关联资源”:

```markdown
| 参考文档 | [`harness/reference/lcharness-architecture.md`](./harness/reference/lcharness-architecture.md) | 当前 `engineering/` 映射到未来 `LcHarness` 的架构参考 |
```

In `engineering/loop/README.md`, revise the “定位” or “目录说明” wording so it explicitly states:

```markdown
- **未来映射**：在独立 `LcHarness` 架构中，`loop engineering` 作为 solution pack 存在，不进入 core。
```

- [ ] **Step 5: Run targeted validation**

Run:

```bash
python3 -c "import yaml; data=yaml.safe_load(open('engineering/harness/config/lcharness-layer-map.yaml', encoding='utf-8')); assert isinstance(data.get('entries'), list) and len(data['entries']) >= 10; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/config/lcharness-layer-map.yaml engineering/harness/config/README.md engineering/README.md engineering/loop/README.md
git commit -m "feat(config): 新增 LcHarness 层次映射与 Phase 1 目录归类"
```

---

## Task 3: Add layer-map validation and make it part of config validation

**Files:**
- Create: `engineering/harness/scripts/validate_lcharness_layer_map.sh`
- Modify: `engineering/harness/scripts/validate_harness_config.sh`
- Modify: `engineering/harness/scripts/README.md`
- Modify: `engineering/harness/rules/README.md`

- [ ] **Step 1: Write the failing validator test first**

Create the test file content below in Task 4 Step 1, then run it here before the validator exists:

Run:

```bash
bash engineering/harness/tests/test_lcharness_layer_map.sh
```

Expected: FAIL because `engineering/harness/scripts/validate_lcharness_layer_map.sh` does not exist yet.

- [ ] **Step 2: Implement the dedicated validator**

Create `engineering/harness/scripts/validate_lcharness_layer_map.sh` with this content:

```bash
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_lcharness_layer_map"

MAP_PATH_DEFAULT="$(harness_path HARNESS_DIR)/config/lcharness-layer-map.yaml"
MAP_PATH="${1:-$MAP_PATH_DEFAULT}"

if ! command -v python3 >/dev/null 2>&1; then
    log_error "未找到 python3，无法校验 lcharness-layer-map.yaml"
    harness_exit 3
fi

if [ ! -f "$MAP_PATH" ]; then
    log_error "层次映射文件不存在: $MAP_PATH"
    harness_exit 1
fi

python3 - "$MAP_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

map_path = Path(sys.argv[1])
repo_root = map_path.parents[3]
allowed_layers = {"core", "pack", "profile", "adapter", "control-plane"}
allowed_kinds = {"directory", "file", "virtual"}
allowed_pack_types = {"platform", "domain", "solution"}

with map_path.open("r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}

errs = []
version = data.get("version")
if isinstance(version, bool) or not isinstance(version, int) or version < 1:
    errs.append(f"version 非法: {version!r}")

entries = data.get("entries")
if not isinstance(entries, list) or not entries:
    errs.append("entries 必须是非空数组")
    entries = []

seen_paths = set()
for idx, entry in enumerate(entries):
    if not isinstance(entry, dict):
        errs.append(f"entries[{idx}] 非对象")
        continue
    path = entry.get("path")
    kind = entry.get("kind")
    layer = entry.get("layer")
    component = entry.get("component")
    target = entry.get("target")
    rationale = entry.get("rationale")
    pack_type = entry.get("pack_type")

    if not isinstance(path, str) or not path.strip():
        errs.append(f"entries[{idx}].path 不能为空")
    elif path in seen_paths:
        errs.append(f"entries[{idx}].path 重复: {path}")
    else:
        seen_paths.add(path)

    if kind not in allowed_kinds:
        errs.append(f"entries[{idx}].kind 非法: {kind!r}")

    if layer not in allowed_layers:
        errs.append(f"entries[{idx}].layer 非法: {layer!r}")

    if not isinstance(component, str) or not component.strip():
        errs.append(f"entries[{idx}].component 不能为空")

    if not isinstance(target, str) or not target.strip():
        errs.append(f"entries[{idx}].target 不能为空")

    if not isinstance(rationale, str) or not rationale.strip():
        errs.append(f"entries[{idx}].rationale 不能为空")

    if layer == "pack":
        if pack_type not in allowed_pack_types:
            errs.append(f"entries[{idx}].pack_type 非法: {pack_type!r}")
    elif pack_type is not None:
        errs.append(f"entries[{idx}] 仅 layer=pack 时允许 pack_type")

    if isinstance(path, str) and kind in {"directory", "file"}:
        if not (repo_root / path).exists():
            errs.append(f"entries[{idx}].path 不存在: {path}")

if errs:
    for err in errs:
        print(err)
    sys.exit(1)

print(f"OK: {len(entries)} entries")
PY
status=$?

if [ "$status" -ne 0 ]; then
    harness_exit 1
fi

harness_exit 0
```

- [ ] **Step 3: Extend config validation to include the new map file**

In `engineering/harness/scripts/validate_harness_config.sh`:

1. Add `lcharness-layer-map.yaml` to `YAML_TARGETS`:

```bash
YAML_TARGETS=(
    "scope-mapping.yaml"
    "doc-sync-mapping.yaml"
    "baseline-status.yaml"
    "lcharness-layer-map.yaml"
)
```

2. After the existing `baseline-status.yaml` extra validation block, add:

```bash
    if [ "$yname" = "lcharness-layer-map.yaml" ]; then
        lm_err=$(python3 -c "
import yaml
with open('$ypath', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f) or {}
errs = []
allowed_layers = {'core', 'pack', 'profile', 'adapter', 'control-plane'}
allowed_kinds = {'directory', 'file', 'virtual'}
entries = data.get('entries', [])
if not isinstance(entries, list) or not entries:
    errs.append('entries 必须是非空数组')
for i, item in enumerate(entries if isinstance(entries, list) else []):
    if not isinstance(item, dict):
        errs.append(f'entries[{i}] 非对象')
        continue
    if item.get('layer') not in allowed_layers:
        errs.append(f'entries[{i}] layer 非法: {item.get(\'layer\')}')
    if item.get('kind') not in allowed_kinds:
        errs.append(f'entries[{i}] kind 非法: {item.get(\'kind\')}')
    if not item.get('path'):
        errs.append(f'entries[{i}] path 为空')
for e in errs:
    print(e)
" 2>&1) || true
        if [ -n "$lm_err" ]; then
            while IFS= read -r l; do
                [ -z "$l" ] && continue
                report_warn "$ypath" "$l"
            done <<< "$lm_err"
        fi
    fi
```

- [ ] **Step 4: Update script and rules indexes**

Add one row to `engineering/harness/scripts/README.md`:

```markdown
| [`validate_lcharness_layer_map.sh`](./validate_lcharness_layer_map.sh) | `LcHarness` Phase 1 层次映射校验（layer/kind/pack_type/path 唯一性与存在性） | `bash engineering/harness/scripts/validate_lcharness_layer_map.sh` |
```

Add one bullet or table note in `engineering/harness/rules/README.md` file description section indicating that `manifest.yaml` is not the only machine-readable contract anymore and `config/lcharness-layer-map.yaml` is the Phase 1 architecture mapping contract referenced by plans/reference docs. Do **not** invent a new mandatory RID unless a real rule file is also introduced.

- [ ] **Step 5: Run validator checks**

Run:

```bash
bash engineering/harness/scripts/validate_lcharness_layer_map.sh
bash engineering/harness/scripts/validate_harness_config.sh
bash engineering/harness/scripts/validate_harness_scripts.sh
```

Expected:
- `validate_lcharness_layer_map.sh`: PASS
- `validate_harness_config.sh`: PASS with `lcharness-layer-map.yaml` included
- `validate_harness_scripts.sh`: PASS, including the new validator script’s bootstrap/`harness_init` compliance

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/scripts/validate_lcharness_layer_map.sh engineering/harness/scripts/validate_harness_config.sh engineering/harness/scripts/README.md engineering/harness/rules/README.md
git commit -m "feat(validator): 新增 LcHarness 层次映射校验器"
```

---

## Task 4: Add tests and surface the new contract in test docs

**Files:**
- Create: `engineering/harness/tests/test_lcharness_layer_map.sh`
- Modify: `engineering/harness/tests/README.md`
- Modify: `engineering/harness/tests/test_validators.sh`

- [ ] **Step 1: Write the dedicated layer-map test**

Create `engineering/harness/tests/test_lcharness_layer_map.sh` with this content:

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VALIDATOR="$REPO_ROOT/engineering/harness/scripts/validate_lcharness_layer_map.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$1"; }

write_invalid_map() {
    cat > "$TMP_DIR/invalid.yaml" <<'YAML'
version: 1
entries:
  - path: ""
    kind: bad-kind
    layer: unknown
    component: ""
    target: ""
    rationale: ""
YAML
}

write_valid_map() {
    cat > "$TMP_DIR/valid.yaml" <<YAML
version: 1
entries:
  - path: engineering/harness/config/
    kind: directory
    layer: core
    component: config-machine-layer
    target: lcharness/core/config
    rationale: machine-readable config
YAML
}

test_validator_exists() {
    [ -x "$VALIDATOR" ] || fail "validator 不存在或不可执行"
    pass "validator exists"
}

test_invalid_map_rejected() {
    write_invalid_map
    if bash "$VALIDATOR" "$TMP_DIR/invalid.yaml" >/dev/null 2>&1; then
        fail "invalid map 应被拒绝"
    fi
    pass "invalid map rejected"
}

test_valid_map_accepted() {
    write_valid_map
    bash "$VALIDATOR" "$TMP_DIR/valid.yaml" >/dev/null 2>&1 || fail "valid map 应通过"
    pass "valid map accepted"
}

main() {
    test_validator_exists
    test_invalid_map_rejected
    test_valid_map_accepted
    printf 'PASS: test_lcharness_layer_map.sh\n'
}

main "$@"
```

- [ ] **Step 2: Run the test once before validator implementation (expected fail)**

Run:

```bash
bash engineering/harness/tests/test_lcharness_layer_map.sh
```

Expected: FAIL before Task 3 Step 2 is implemented.

- [ ] **Step 3: Add the new test to README and validator regression**

In `engineering/harness/tests/README.md`, add a file row:

```markdown
| [`test_lcharness_layer_map.sh`](./test_lcharness_layer_map.sh) | `LcHarness` 层次映射测试（非法 layer/kind 拒绝、最小合法映射通过） | `bash test_lcharness_layer_map.sh` |
```

Update the regression matrix with one row:

```markdown
| `test_lcharness_layer_map.sh` | `lcharness-layer-map.yaml` / `validate_lcharness_layer_map.sh` | 3 | — | ✅ |
```

In `engineering/harness/tests/test_validators.sh`, add one case that runs:

```bash
bash engineering/harness/scripts/validate_lcharness_layer_map.sh
```

and treats non-zero exit as failure.

- [ ] **Step 4: Run test and validator regression after implementation**

Run:

```bash
bash engineering/harness/tests/test_lcharness_layer_map.sh
bash engineering/harness/tests/test_validators.sh
bash engineering/harness/tests/run_all_tests.sh
```

Expected:
- `test_lcharness_layer_map.sh`: PASS
- `test_validators.sh`: PASS
- `run_all_tests.sh`: PASS, including the new layer-map test in the aggregated run

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/tests/test_lcharness_layer_map.sh engineering/harness/tests/README.md engineering/harness/tests/test_validators.sh
git commit -m "feat(test): 新增 LcHarness 层次映射测试与回归覆盖"
```

---

## Task 5: Reconcile README navigation across engineering boundaries

**Files:**
- Modify: `engineering/README.md`
- Modify: `engineering/harness/README.md`
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: Add a future-mapping note to engineering/README.md**

Add a short note in `## 边界与依赖` after the existing ownership bullets:

```markdown
> 面向未来独立 `LcHarness` 架构：当前 `engineering/harness/` 视为 core 候选基础设施层，`engineering/loop/` 视为 solution pack 候选层；具体映射见 `harness/reference/lcharness-architecture.md`。
```

- [ ] **Step 2: Make the loop README explicit about solution-pack status**

In `engineering/loop/README.md`, extend the “定位” block with a line that makes the future mapping explicit:

```markdown
- **未来映射**：在独立 `LcHarness` 架构中，`loop engineering` 作为 solution pack 存在，由 core 提供公共基础设施，但不进入 core。
```

- [ ] **Step 3: Re-run docs validation after all README changes**

Run:

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

Expected: PASS with no new README index/link mismatch caused by the added architecture references.

- [ ] **Step 4: Commit**

```bash
git add engineering/README.md engineering/harness/README.md engineering/loop/README.md
git commit -m "docs(engineering): 对齐 LcHarness Phase 1 分层导航"
```

---

## Final verification

- [ ] **Step 1: Run the full Phase 1 validation set**

Run:

```bash
bash engineering/harness/scripts/validate_harness_docs.sh && bash engineering/harness/scripts/validate_harness_config.sh && bash engineering/harness/scripts/validate_harness_scripts.sh && bash engineering/harness/scripts/validate_lcharness_layer_map.sh && bash engineering/harness/tests/test_lcharness_layer_map.sh && bash engineering/harness/tests/test_validators.sh
```

Expected:
- All commands exit `0`
- No new warnings about missing links, malformed YAML, invalid layer values, missing bootstrap, or failing layer-map tests

- [ ] **Step 2: Inspect resulting diff scope**

Run:

```bash
git diff -- docs/specs/2026-07-02-lcharness-framework-design.md docs/plans/2026-07-02-lcharness-phase1-architecture-refactor-plan.md engineering/README.md engineering/harness/README.md engineering/loop/README.md engineering/harness/reference/README.md engineering/harness/reference/lcharness-architecture.md engineering/harness/config/README.md engineering/harness/config/lcharness-layer-map.yaml engineering/harness/scripts/README.md engineering/harness/scripts/validate_harness_config.sh engineering/harness/scripts/validate_lcharness_layer_map.sh engineering/harness/tests/README.md engineering/harness/tests/test_validators.sh engineering/harness/tests/test_lcharness_layer_map.sh engineering/harness/rules/README.md
```

Expected: Diff is limited to Phase 1 architecture documentation, layer mapping, validator integration, and test coverage only.

- [ ] **Step 3: Final commit**

```bash
git add engineering/README.md engineering/harness/README.md engineering/loop/README.md engineering/harness/reference/README.md engineering/harness/reference/lcharness-architecture.md engineering/harness/config/README.md engineering/harness/config/lcharness-layer-map.yaml engineering/harness/scripts/README.md engineering/harness/scripts/validate_harness_config.sh engineering/harness/scripts/validate_lcharness_layer_map.sh engineering/harness/tests/README.md engineering/harness/tests/test_validators.sh engineering/harness/tests/test_lcharness_layer_map.sh engineering/harness/rules/README.md
git commit -m "feat(lcharness): 建立 Phase 1 架构分层与层次映射校验"
```

---

## Notes for the executor

1. Do not start Phase 2 control-plane implementation during this plan.
2. Do not add any tracked integration file to target business repositories.
3. Keep `loop` as a solution-pack classification only; do not flatten its implementation into harness core.
4. If any current workflow mapping feels ambiguous, encode the ambiguity in the reference/layer-map rationale instead of performing premature file moves.

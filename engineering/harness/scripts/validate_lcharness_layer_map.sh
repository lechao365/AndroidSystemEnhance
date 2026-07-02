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

REPO_ROOT="${2:-$(harness_repo_root)}"

python3 - "$MAP_PATH" "$REPO_ROOT" <<'PY'
import sys
from pathlib import Path
import yaml

map_path = Path(sys.argv[1])
repo_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
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
#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "check_access"

path=""
category=""
while [ $# -gt 0 ]; do
    case "$1" in
        --path) path="$2"; shift 2 ;;
        --category) category="$2"; shift 2 ;;
        *) echo "Usage: $0 --path <path> --category <category>"; harness_exit 2 ;;
    esac
done

[ -z "$path" ] && { echo "ERROR: --path 必填" >&2; harness_exit 2; }
[ -z "$category" ] && { echo "ERROR: --category 必填" >&2; harness_exit 2; }

MANIFEST="$(harness_path HARNESS_DIR)/rules/manifest.yaml"
[ ! -f "$MANIFEST" ] && { echo "ERROR: manifest.yaml 不存在: $MANIFEST" >&2; harness_exit 3; }

output=$(python3 -c "
import sys, yaml, json
from fnmatch import fnmatch

with open('$MANIFEST') as f:
    data = yaml.safe_load(f)

target_path = '$path'
target_category = '$category'

# phase 1: scope_category + match 双匹配
matched = None
for ctx in data.get('contexts', []):
    if ctx.get('scope_category') == target_category and fnmatch(target_path, ctx.get('match', '*')):
        matched = ctx
        break

# phase 2: fallback 到仅 scope_category 匹配（保留降级兼容）
if matched is None:
    for ctx in data.get('contexts', []):
        if ctx.get('scope_category') == target_category:
            matched = ctx
            break

if matched is None:
    output = {'allowed': False, 'reason': 'no matching context for category: ' + target_category}
else:
    output = {
        'allowed': True,
        'access': matched.get('access', 'unknown'),
        'rules': matched.get('rules', []),
        'workflow': matched.get('workflow', []),
        'require_plan': matched.get('require_plan', False),
        'require_confirmation': matched.get('require_confirmation', False),
        'require_evidence': matched.get('require_evidence', False),
    }

print(json.dumps(output, indent=2, ensure_ascii=False))
" 2>&1) || harness_exit 1

echo "$output"
harness_exit 0
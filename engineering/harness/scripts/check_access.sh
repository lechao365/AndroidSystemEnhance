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

result=$(python3 -c "
import sys, yaml
with open('$MANIFEST') as f:
    data = yaml.safe_load(f)

category = '$category'

matched = None
for ctx in data.get('contexts', []):
    if ctx.get('scope_category') == category:
        matched = ctx
        break

if matched is None:
    print('{\"allowed\": false, \"reason\": \"no matching context for category: ' + category + '\"}')
    sys.exit(0)

output = {
    'allowed': True,
    'access': matched.get('access', 'unknown'),
    'rules': matched.get('rules', []),
    'workflow': matched.get('workflow', []),
    'require_plan': matched.get('require_plan', False),
    'require_confirmation': matched.get('require_confirmation', False),
    'require_evidence': matched.get('require_evidence', False),
}
import json
print(json.dumps(output, indent=2, ensure_ascii=False))
")

echo "$result"
harness_exit 0
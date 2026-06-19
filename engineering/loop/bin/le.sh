#!/bin/bash
# le.sh — Loop Engineering v2 统一 CLI 入口
# 用法:
#   le.sh run --suite boot-success --fixture <jsonl> --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
#   le.sh run --suite boot-success --host 127.0.0.1 --port 9700 --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../harness/lib/harness_bootstrap.sh"

harness_init "le"

CORE_ROOT="$SCRIPT_DIR/../core/python"
PROVIDER_ROOT="$SCRIPT_DIR/../connection/providers/rp5-serial/python"
export PYTHONPATH="$CORE_ROOT:$PROVIDER_ROOT${PYTHONPATH:+:$PYTHONPATH}"

python3 -m loop_core.cli "$@"
rc=$?

harness_exit "$rc"

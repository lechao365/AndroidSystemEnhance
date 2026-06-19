#!/bin/bash
# loop_rp5_serial_status.sh — 查询 rp5-serial host 状态
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../../harness/lib/harness_bootstrap.sh"

harness_init "loop-rp5-serial-status"

PYTHON_ROOT="$SCRIPT_DIR/../python"
PYTHONPATH="$PYTHON_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m rp5_serial.client.status "$@"
rc=$?

harness_exit $rc

#!/bin/bash
# loop_rp5_serial_status.sh — 查询 rp5-serial host 状态
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../../harness/lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../../harness/lib/shell/harness_bootstrap.sh"

harness_init "loop-rp5-serial-status"

PYTHONPATH="$(harness_pythonpath)${PYTHONPATH:+:$PYTHONPATH}" python3 -m rp5_serial.client.status "$@"
rc=$?

harness_exit $rc

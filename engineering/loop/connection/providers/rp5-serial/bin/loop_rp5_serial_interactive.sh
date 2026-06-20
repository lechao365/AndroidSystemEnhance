#!/bin/bash
# loop_rp5_serial_interactive.sh — rp5-serial 交互式入口
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../../harness/lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../../harness/lib/shell/harness_bootstrap.sh"

harness_init "loop-rp5-serial-interactive"

PYTHONPATH="$(harness_pythonpath)${PYTHONPATH:+:$PYTHONPATH}" python3 -m rp5_serial.client.interactive "$@"
rc=$?

harness_exit $rc

#!/bin/bash
# loop_rp5_serial_interactive.sh — rp5-serial 交互式入口
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../../harness/lib/harness_bootstrap.sh"

harness_init "loop-rp5-serial-interactive"

PYTHON_ROOT="$SCRIPT_DIR/../python"
PYTHONPATH="$PYTHON_ROOT${PYTHONPATH:+:$PYTHONPATH}" python -m rp5_serial.client.interactive "$@"
rc=$?

harness_exit $rc

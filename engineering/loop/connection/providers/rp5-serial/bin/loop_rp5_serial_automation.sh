#!/bin/bash
# loop_rp5_serial_automation.sh — rp5-serial 自动化入口
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../../harness/lib/harness_bootstrap.sh"

harness_init "loop-rp5-serial-automation"

PYTHON_ROOT="$SCRIPT_DIR/../python"
PYTHONPATH="$PYTHON_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m rp5_serial.client.automation "$@"
rc=$?

harness_exit $rc

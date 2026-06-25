#!/bin/bash
# lcview-adb-run — serial bootstrap → adb feature run → serial fallback
# 用法:
#   run_lcview_adb_suite.sh --serial-host 127.0.0.1 --serial-port 9700 [--adb-endpoint <ip>:5555] [--artifacts-dir <dir>]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../harness/lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../harness/lib/shell/harness_bootstrap.sh"

harness_init "lcview-adb-run"

SERIAL_HOST="127.0.0.1"
SERIAL_PORT="9700"
ADB_ENDPOINT=""
ARTIFACTS_DIR="$(harness_path RUNS_DIR)/lcview-adb-run"
LOOP_DIR="$(harness_path LOOP_DIR)"
LOOP_CASES_DIR="$(harness_path LOOP_CASES_DIR)"
SERIAL_PROFILE="$LOOP_DIR/connection/profiles/devices/rp5/default.json"
ADB_PROFILE="$LOOP_DIR/connection/profiles/devices/rp5/adb.json"
CASE_DIR="$LOOP_CASES_DIR"
BOOTSTRAP_SUITE="$LOOP_CASES_DIR/system/network-adbd-success.yaml"
FEATURE_SUITE="$LOOP_CASES_DIR/features/lcview/end_to_end.yaml"
FALLBACK_SUITE="$LOOP_CASES_DIR/system/boot-success.yaml"
LOOP_SCRIPTS_DIR="$(harness_path LOOP_SCRIPTS_DIR)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial-host) SERIAL_HOST="$2"; shift 2 ;;
    --serial-port) SERIAL_PORT="$2"; shift 2 ;;
    --adb-endpoint) ADB_ENDPOINT="$2"; shift 2 ;;
    --artifacts-dir) ARTIFACTS_DIR="$2"; shift 2 ;;
    --serial-profile) SERIAL_PROFILE="$2"; shift 2 ;;
    --adb-profile) ADB_PROFILE="$2"; shift 2 ;;
    *) log_error "unknown arg: $1"; harness_exit 3 ;;
  esac
done

BOOTSTRAP_OUT="$ARTIFACTS_DIR/bootstrap"
FEATURE_OUT="$ARTIFACTS_DIR/feature"
FALLBACK_OUT="$ARTIFACTS_DIR/fallback"
mkdir -p "$BOOTSTRAP_OUT" "$FEATURE_OUT" "$FALLBACK_OUT"

step_begin "bootstrap"
bash "$LOOP_SCRIPTS_DIR/le.sh" run \
  --suite "$BOOTSTRAP_SUITE" \
  --host "$SERIAL_HOST" \
  --port "$SERIAL_PORT" \
  --device-profile "$SERIAL_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$BOOTSTRAP_OUT"
bootstrap_rc=$?
step_end "$bootstrap_rc"

if [[ $bootstrap_rc -ne 0 ]]; then
  log_error "BOOTSTRAP_FAIL (rc=$bootstrap_rc)"
  harness_status_emit FAIL "bootstrap"
  harness_exit "$bootstrap_rc"
fi

if [[ -z "$ADB_ENDPOINT" ]]; then
  step_begin "discover-adb-endpoint"
  DISCOVERED_IP="$(python3 "$LOOP_SCRIPTS_DIR/rp5_serial_helper.py" device-ip --host "$SERIAL_HOST" --port "$SERIAL_PORT" 2>/dev/null || true)"
  if [[ -z "$DISCOVERED_IP" || "$DISCOVERED_IP" == "NO_IP_FOUND" ]]; then
    log_error "ADB_CONNECT_FAIL: cannot discover device IP"
    harness_status_emit FAIL "discover-adb-endpoint"
    harness_exit 1
  fi
  ADB_ENDPOINT="${DISCOVERED_IP}:5555"
  step_end 0
fi

log_info "adb endpoint: $ADB_ENDPOINT"

step_begin "feature"
bash "$LOOP_SCRIPTS_DIR/le.sh" run \
  --suite "$FEATURE_SUITE" \
  --device-profile "$ADB_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$FEATURE_OUT" \
  --adb-endpoint "$ADB_ENDPOINT"
feature_rc=$?
step_end "$feature_rc"

if [[ $feature_rc -ne 0 ]]; then
  log_warn "feature run failed (rc=$feature_rc), collecting serial fallback evidence"
  step_begin "fallback"
  bash "$LOOP_SCRIPTS_DIR/le.sh" run \
    --suite "$FALLBACK_SUITE" \
    --host "$SERIAL_HOST" \
    --port "$SERIAL_PORT" \
    --device-profile "$SERIAL_PROFILE" \
    --case-dirs "$CASE_DIR" \
    --artifacts-dir "$FALLBACK_OUT"
  fallback_rc=$?
  step_end "$fallback_rc"
fi

log_result "lcview-adb-run" "feature_rc=$feature_rc"
harness_exit "$feature_rc"
#!/bin/bash
# lcview-adb-run — serial bootstrap → adb feature run → serial fallback
# 用法:
#   run_lcview_adb_suite.sh --serial-host 127.0.0.1 --serial-port 9700 [--adb-endpoint 192.168.1.55:5555] [--artifacts-dir <dir>]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"

harness_init "lcview-adb-run"

SERIAL_HOST="127.0.0.1"
SERIAL_PORT="9700"
ADB_ENDPOINT=""
ARTIFACTS_DIR="$(harness_path RUNS_DIR)/lcview-adb-run"
SERIAL_PROFILE="$(harness_path ENGINEERING_DIR)/loop/connection/profiles/devices/rp5/default.json"
ADB_PROFILE="$(harness_path ENGINEERING_DIR)/loop/connection/profiles/devices/rp5/adb.json"
CASE_DIR="$(harness_path ENGINEERING_DIR)/loop/cases"
BOOTSTRAP_SUITE="$(harness_path ENGINEERING_DIR)/loop/cases/system/network-adbd-success.yaml"
FEATURE_SUITE="$(harness_path ENGINEERING_DIR)/loop/cases/features/lcview/end_to_end.yaml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial-host) SERIAL_HOST="$2"; shift 2 ;;
    --serial-port) SERIAL_PORT="$2"; shift 2 ;;
    --adb-endpoint) ADB_ENDPOINT="$2"; shift 2 ;;
    --artifacts-dir) ARTIFACTS_DIR="$2"; shift 2 ;;
    --serial-profile) SERIAL_PROFILE="$2"; shift 2 ;;
    --adb-profile) ADB_PROFILE="$2"; shift 2 ;;
    *) log_error "unknown arg: $1"; harness_exit 2 ;;
  esac
done

BOOTSTRAP_OUT="$ARTIFACTS_DIR/bootstrap"
FEATURE_OUT="$ARTIFACTS_DIR/feature"
FALLBACK_OUT="$ARTIFACTS_DIR/fallback"
mkdir -p "$BOOTSTRAP_OUT" "$FEATURE_OUT" "$FALLBACK_OUT"

step_begin "bootstrap" "run serial network-adbd bootstrap"
bash "$(harness_path HARNESS_DIR)/scripts/le.sh" run \
  --suite "$BOOTSTRAP_SUITE" \
  --host "$SERIAL_HOST" \
  --port "$SERIAL_PORT" \
  --device-profile "$SERIAL_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$BOOTSTRAP_OUT"
bootstrap_rc=$?
step_end "bootstrap" "$bootstrap_rc"

if [[ $bootstrap_rc -ne 0 ]]; then
  log_error "BOOTSTRAP_FAIL (rc=$bootstrap_rc)"
  harness_status_emit FAIL "bootstrap"
  harness_exit "$bootstrap_rc"
fi

if [[ -z "$ADB_ENDPOINT" ]]; then
  step_begin "discover-adb-endpoint" "discover adb endpoint from serial helper"
  DISCOVERED_IP="$(python3 "$(harness_path HARNESS_DIR)/scripts/rp5_serial_helper.py" device-ip --host "$SERIAL_HOST" --port "$SERIAL_PORT" 2>/dev/null || true)"
  if [[ -z "$DISCOVERED_IP" || "$DISCOVERED_IP" == "NO_IP_FOUND" ]]; then
    log_error "ADB_CONNECT_FAIL: cannot discover device IP"
    harness_status_emit FAIL "discover-adb-endpoint"
    harness_exit 1
  fi
  ADB_ENDPOINT="${DISCOVERED_IP}:5555"
  step_end "discover-adb-endpoint" 0
fi

log_info "adb endpoint: $ADB_ENDPOINT"

step_begin "feature" "run lcview adb feature suite"
bash "$(harness_path HARNESS_DIR)/scripts/le.sh" run \
  --suite "$FEATURE_SUITE" \
  --device-profile "$ADB_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$FEATURE_OUT" \
  --adb-endpoint "$ADB_ENDPOINT"
feature_rc=$?
step_end "feature" "$feature_rc"

if [[ $feature_rc -ne 0 ]]; then
  log_warn "feature run failed (rc=$feature_rc), collecting serial fallback evidence"
  step_begin "fallback" "collect serial fallback context"
  bash "$(harness_path HARNESS_DIR)/scripts/le.sh" run \
    --suite "$(harness_path ENGINEERING_DIR)/loop/cases/system/boot-success.yaml" \
    --host "$SERIAL_HOST" \
    --port "$SERIAL_PORT" \
    --device-profile "$SERIAL_PROFILE" \
    --case-dirs "$CASE_DIR" \
    --artifacts-dir "$FALLBACK_OUT"
  step_end "fallback" 0
fi

log_result "lcview-adb-run" "$feature_rc" "feature_rc"
harness_exit "$feature_rc"
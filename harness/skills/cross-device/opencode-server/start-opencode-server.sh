#!/bin/bash
set -uo pipefail

# ============================================================================
# start-opencode-server.sh — 一键拉起 OpenCode WebUI（WSL2 + Windows Tailscale）
# 职责:
#   1) 在 WSL2 内以 systemd user service 托管 opencode web
#   2) 在 Windows 宿主上配置 tailscale serve -> localhost:<port>
#   3) 输出手机可访问的 tailnet HTTPS URL（WebUI 认证复用 server.env）
# 目标 workspace: 本项目工程根（AGENTS.md 锚点自动定位，供跨设备 emit/apply 协作）
# 详见:
#   - SKILL.md（同目录: 工作流/配置/退出码/环境变量说明）
#   - harness/reference/remote-access-reference.md（RMT-001~008 安全约束）
# 本脚本仅限 WSL/Linux 环境: 依赖 systemd/tailscale/PowerShell
# 实现拆至 lib/shell/（规模拆分）: systemd 侧 + tailscale 侧两个 lib，
# 本入口保留内嵌最小运行时 / 配置 / 参数解析 / unit 原子写 / 主流程编排。
# ============================================================================

usage() {
    cat <<'EOF'
  用法:
  bash <skill>/start-opencode-server.sh [options]

功能:
  - 在 WSL2 内生成/更新 systemd user service 并托管 opencode web
  - 在 Windows 宿主上配置 tailscale serve HTTPS -> localhost:PORT
  - 输出手机可访问的 tailnet HTTPS 地址

选项:
  --port <port>                 WebUI 监听端口，默认 4096
  --service-name <name>         systemd user service 名称，默认 opencode-web
  --status-only                 仅检查当前 WSL service / 监听端口 / tailscale serve 状态
  --restart-serve-only          仅重配 Windows tailscale serve；不重启 WSL service
  -h, --help                    显示帮助

说明:
  1) 认证账号密码来自 server.env（默认 ~/.config/opencode/server.env，
     可用 ENV_OPENCODE_SERVER_ENV_FILE 覆盖）
  2) WSL2 侧 opencode web 仅监听 127.0.0.1:PORT
  3) 手机访问需已加入同一 Tailscale tailnet
  4) 默认拉起本项目工程根（AGENTS.md 锚点定位）的 WebUI；配置项可经环境变量
     覆盖（ENV_OPENCODE_SERVER_PORT 等，见 SKILL.md 配置节）
EOF
}

# 提前拦截 --help/-h：在 lc_init 之前退出，避免创建日志/artifact 与打印运行汇总等副作用
for _arg in "$@"; do
    case "$_arg" in
        -h|--help)
            usage
            exit 0
            ;;
    esac
done
unset _arg

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================================
# 内嵌最小运行时（脱离 LcSkills core，语义对齐 harness/lib/harness_lib.py）
# 提供: 工程根定位 / 日志 / 步骤追踪 / 错误处理 / 退出汇总；
# 日志落 harness/log/opencode-server/（gitignore 工作态，不入库）
# ============================================================================

_LOG_FILE=""
_INIT_TS=0

locate_project_root() {
    # 工程根: ENV_OPENCODE_SERVER_PROJECT_ROOT 覆盖 > AGENTS.md 锚点向上探测
    if [ -n "${ENV_OPENCODE_SERVER_PROJECT_ROOT:-}" ] && [ -d "$ENV_OPENCODE_SERVER_PROJECT_ROOT" ]; then
        printf '%s' "$ENV_OPENCODE_SERVER_PROJECT_ROOT"
        return 0
    fi
    local root="$SCRIPT_DIR"
    while [ "$root" != "/" ]; do
        if [ -f "$root/AGENTS.md" ]; then
            printf '%s' "$root"
            return 0
        fi
        root="$(dirname "$root")"
    done
    log_error "无法定位工程根（AGENTS.md 锚点缺失；可设 ENV_OPENCODE_SERVER_PROJECT_ROOT 覆盖）"
    return 1
}

_log() {
    local line="[$1] $2"
    printf '%s\n' "$line" >&2
    if [[ -n "$_LOG_FILE" ]]; then
        if ! printf '%s %s\n' "$(date '+%F %T')" "$line" >> "$_LOG_FILE" 2>/dev/null; then
            printf '%s\n' "[ERROR] 日志写入失败: $_LOG_FILE" >&2
        fi
    fi
    return 0
}

log_info()  { _log INFO "$1"; }
log_warn()  { _log WARN "$1"; }
log_error() { _log ERROR "$1"; }

log_result() {
    local out="$1"
    shift
    local field
    for field in "$@"; do
        out+=" $field"
    done
    _log RESULT "$out"
}

_STEP_STACK=()
_STEP_TS=()
_STEP_IDX=0

step_begin() {
    _STEP_IDX=$((_STEP_IDX + 1))
    _STEP_STACK+=("$1")
    _STEP_TS+=("$(date +%s%3N)")
    printf '\n========== STEP %d: %s ==========\n' "$_STEP_IDX" "$1" >&2
    return 0
}

step_end() {
    local rc="$1"
    local title="" start=0 elapsed=0 mark="  OK"
    if [ ${#_STEP_STACK[@]} -gt 0 ]; then
        title="${_STEP_STACK[-1]}"
        unset '_STEP_STACK[-1]'
    fi
    if [ ${#_STEP_TS[@]} -gt 0 ]; then
        start="${_STEP_TS[-1]}"
        unset '_STEP_TS[-1]'
    fi
    [[ "$start" =~ ^[0-9]+$ ]] || start=0
    elapsed=$(( $(date +%s%3N) - start ))
    [ "$rc" -eq 0 ] || mark="FAIL"
    printf '[%s] %s (%d.%ds)\n' "$mark" "$title" $((elapsed / 1000)) $(((elapsed % 1000) / 100)) >&2
    return 0
}

on_err() {
    log_error "错误@L$1: $2 (rc=$3)"
    lc_exit 1
}

lc_init() {
    local root=""
    root="$(locate_project_root)" || exit 3
    _INIT_TS="$(date +%s%3N)"
    local log_dir="$root/harness/log/opencode-server"
    if mkdir -p "$log_dir" 2>/dev/null; then
        _LOG_FILE="$log_dir/$(date +%Y%m%d-%H%M%S).log"
    fi
    log_info "start: $1"
}

lc_exit() {
    local code="${1:-0}"
    local elapsed_ms=0 mark="  OK"
    if [[ "$_INIT_TS" =~ ^[0-9]+$ ]]; then
        elapsed_ms=$(( $(date +%s%3N) - _INIT_TS ))
    fi
    [ "$code" -eq 0 ] || mark="FAIL"
    printf '\n[%s] start_opencode_server 退出码=%s 耗时=%d.%ds\n' \
        "$mark" "$code" $((elapsed_ms / 1000)) $(((elapsed_ms % 1000) / 100)) >&2
    exit "$code"
}

# ============================================================================
# 拆出件（位于 lib/shell/，由入口 source 加载）
# ============================================================================
source "$SCRIPT_DIR/lib/shell/start_opencode_server_systemd.sh"
source "$SCRIPT_DIR/lib/shell/start_opencode_server_tailscale.sh"

# 初始化（创建日志目录 harness/log/opencode-server/ 并记录起始时间戳）
lc_init "start_opencode_server"

# ============================================================================
# 工程定位（AGENTS.md 锚点 > ENV_OPENCODE_SERVER_PROJECT_ROOT 覆盖）
# ============================================================================
# 目标 workspace：固定为本项目工程根（WebUI 打开的目录，供跨设备协作）
TARGET_ROOT="$(locate_project_root)" || lc_exit 3
readonly TARGET_ROOT

# 配置默认值（环境变量可覆盖，见 SKILL.md 配置节）
readonly DEFAULT_PORT="${ENV_OPENCODE_SERVER_PORT:-4096}"
readonly DEFAULT_SERVICE_NAME="${ENV_OPENCODE_SERVER_NAME:-opencode-web}"
readonly SERVER_HOST="${ENV_OPENCODE_SERVER_HOST:-127.0.0.1}"
readonly SERVER_ENV_FILE="${ENV_OPENCODE_SERVER_ENV_FILE:-$HOME/.config/opencode/server.env}"
readonly SYSTEMD_USER_DIR="${ENV_SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"
readonly TAILSCALE_SERVE_PORT="${ENV_TAILSCALE_SERVE_PORT:-443}"

PORT="$DEFAULT_PORT"
SERVICE_NAME="$DEFAULT_SERVICE_NAME"
STATUS_ONLY=0
RESTART_SERVE_ONLY=0

SERVICE_UNIT=""
SERVICE_FILE=""
OPENCODE_BIN=""
OPENCODE_SERVER_USERNAME=""
OPENCODE_SERVER_PASSWORD=""
TAILSCALE_EXE=""
SERVE_URL=""
SERVE_STATUS_OUTPUT=""

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --port)
                shift
                [[ $# -gt 0 ]] || {
                    log_error "--port 缺少参数"
                    lc_exit 3
                }
                PORT="$1"
                ;;
            --service-name)
                shift
                [[ $# -gt 0 ]] || {
                    log_error "--service-name 缺少参数"
                    lc_exit 3
                }
                SERVICE_NAME="$1"
                ;;
            --status-only)
                STATUS_ONLY=1
                ;;
            --restart-serve-only)
                RESTART_SERVE_ONLY=1
                ;;
            *)
                log_error "未知参数: $1"
                usage
                lc_exit 3
                ;;
        esac
        shift
    done

    [[ "$PORT" =~ ^[0-9]+$ ]] || {
        log_error "--port 必须是数字: $PORT"
        lc_exit 3
    }

    if (( PORT < 1 || PORT > 65535 )); then
        log_error "--port 超出有效范围: $PORT"
        lc_exit 3
    fi

    [[ "$SERVICE_NAME" =~ ^[A-Za-z0-9@._-]+$ ]] || {
        log_error "--service-name 含非法字符: $SERVICE_NAME"
        lc_exit 3
    }

    if [[ $STATUS_ONLY -eq 1 && $RESTART_SERVE_ONLY -eq 1 ]]; then
        log_error "--status-only 与 --restart-serve-only 不能同时使用"
        lc_exit 3
    fi

    SERVICE_UNIT="${SERVICE_NAME}.service"
    SERVICE_FILE="${SYSTEMD_USER_DIR}/${SERVICE_UNIT}"
}

write_systemd_service() {
    step_begin "生成/更新 systemd user service"

    mkdir -p "$SYSTEMD_USER_DIR" || {
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "mkdir -p $SYSTEMD_USER_DIR" "$rc"
    }

    # 原子写：同目录临时文件 + mv（中断不留半写态；EnvironmentFile 用 SERVER_ENV_FILE
    # 解析后的实际路径，即 ENV_OPENCODE_SERVER_ENV_FILE 或默认值）
    local tmp_file="$SYSTEMD_USER_DIR/.${SERVICE_UNIT}.tmp"
    if ! cat > "$tmp_file" <<EOF
[Unit]
Description=OpenCode Web UI Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$SERVER_ENV_FILE
WorkingDirectory=$TARGET_ROOT
ExecStart=$OPENCODE_BIN web --hostname $SERVER_HOST --port $PORT
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF
    then
        rm -f "$tmp_file"
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "写入 $SERVICE_FILE" "$rc"
    fi

    if ! mv -f "$tmp_file" "$SERVICE_FILE"; then
        rm -f "$tmp_file"
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "mv -f $tmp_file $SERVICE_FILE" "$rc"
    fi

    systemctl --user daemon-reload >/dev/null 2>&1 || {
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "systemctl --user daemon-reload" "$rc"
    }

    log_info "service_file=$SERVICE_FILE"
    step_end 0
}

parse_args "$@"
run_prechecks
load_server_env

if [[ $STATUS_ONLY -eq 1 ]]; then
check_local_service_ready
configure_or_check_tailscale_serve "status"
emit_summary
lc_exit 0
fi

stop_legacy_web_processes

if [[ $RESTART_SERVE_ONLY -eq 1 ]]; then
    check_local_service_ready
    configure_or_check_tailscale_serve "configure"
    emit_summary
    lc_exit 0
fi

write_systemd_service
restart_systemd_service
check_local_service_ready
configure_or_check_tailscale_serve "configure"
emit_summary
lc_exit 0

#!/bin/bash
set -uo pipefail

# ============================================================================
# start-opencode-server.sh — 一键拉起 OpenCode WebUI（WSL2 + Windows Tailscale）
# 职责:
#   1) 在 WSL2 内以 systemd user service 托管 opencode web
#   2) 在 Windows 宿主上配置 tailscale serve -> localhost:<port>
#   3) 输出手机可访问的 tailnet HTTPS URL（WebUI 认证复用 server.env）
# 详见:
#   - engineering/harness/rules/script-observability.md (OBS-001/002)
#   - engineering/harness/rules/path-management.md (PATH-001)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "start_opencode_server"

readonly DEFAULT_PORT="4096"
readonly DEFAULT_SERVICE_NAME="opencode-web"
readonly SERVER_HOST="127.0.0.1"
readonly SERVER_ENV_FILE="$HOME/.config/opencode/server.env"
readonly SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
readonly TAILSCALE_SERVE_PORT="443"

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

usage() {
    cat <<'EOF'
用法:
  bash engineering/harness/scripts/start-opencode-server.sh [options]

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
  1) 认证账号密码来自 ~/.config/opencode/server.env
  2) WSL2 侧 opencode web 仅监听 127.0.0.1:PORT
  3) 手机访问需已加入同一 Tailscale tailnet
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --port)
                shift
                [[ $# -gt 0 ]] || {
                    log_error "--port 缺少参数"
                    harness_exit 3
                }
                PORT="$1"
                ;;
            --service-name)
                shift
                [[ $# -gt 0 ]] || {
                    log_error "--service-name 缺少参数"
                    harness_exit 3
                }
                SERVICE_NAME="$1"
                ;;
            --status-only)
                STATUS_ONLY=1
                ;;
            --restart-serve-only)
                RESTART_SERVE_ONLY=1
                ;;
            -h|--help)
                usage
                harness_exit 0
                ;;
            *)
                log_error "未知参数: $1"
                usage
                harness_exit 3
                ;;
        esac
        shift
    done

    [[ "$PORT" =~ ^[0-9]+$ ]] || {
        log_error "--port 必须是数字: $PORT"
        harness_exit 3
    }

    if (( PORT < 1 || PORT > 65535 )); then
        log_error "--port 超出有效范围: $PORT"
        harness_exit 3
    fi

    [[ "$SERVICE_NAME" =~ ^[A-Za-z0-9@._-]+$ ]] || {
        log_error "--service-name 含非法字符: $SERVICE_NAME"
        harness_exit 3
    }

    if [[ $STATUS_ONLY -eq 1 && $RESTART_SERVE_ONLY -eq 1 ]]; then
        log_error "--status-only 与 --restart-serve-only 不能同时使用"
        harness_exit 3
    fi

    SERVICE_UNIT="${SERVICE_NAME}.service"
    SERVICE_FILE="${SYSTEMD_USER_DIR}/${SERVICE_UNIT}"
}

require_command() {
    local cmd="$1"
    local hint="$2"

    command -v "$cmd" >/dev/null 2>&1 || {
        log_error "$hint"
        harness_exit 3
    }
}

load_server_env() {
    step_begin "加载 server.env 凭据"

    [[ -f "$SERVER_ENV_FILE" ]] || {
        step_end 1
        log_error "未找到 $SERVER_ENV_FILE"
        log_error "请先配置 OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD"
        harness_exit 3
    }

    set -a
    # shellcheck disable=SC1090
    source "$SERVER_ENV_FILE" || {
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "source $SERVER_ENV_FILE" "$rc"
    }
    set +a

    [[ -n "$OPENCODE_SERVER_USERNAME" ]] || {
        step_end 1
        log_error "server.env 缺少 OPENCODE_SERVER_USERNAME"
        harness_exit 3
    }

    [[ -n "$OPENCODE_SERVER_PASSWORD" ]] || {
        step_end 1
        log_error "server.env 缺少 OPENCODE_SERVER_PASSWORD"
        harness_exit 3
    }

    log_info "已加载 server.env（auth_user=$OPENCODE_SERVER_USERNAME）"
    step_end 0
}

run_prechecks() {
    step_begin "前置检查"

    require_command opencode "opencode 命令未找到（请确认 WSL2 内已安装 opencode-ai）"
    require_command systemctl "systemctl 未找到（请确认 WSL2 已启用 systemd）"
    require_command ss "ss 未找到（请安装 iproute2）"
    require_command powershell.exe "powershell.exe 未找到（请确认当前环境运行于 WSL2）"

    OPENCODE_BIN="$(command -v opencode)"

    systemctl --user show-environment >/dev/null 2>&1 || {
        step_end 1
        log_error "systemctl --user 当前不可用"
        log_error "请确认当前 WSL2 用户会话已启用 systemd，并可执行 systemctl --user"
        harness_exit 3
    }

    if [[ ! -d "$REPO_ROOT/.opencode" ]]; then
        step_end 1
        log_error "项目根缺少 .opencode 目录: $REPO_ROOT/.opencode"
        log_error "opencode web 必须从项目根启动，才能加载项目级 commands/agents/skills"
        harness_exit 3
    fi

    if [[ ! -d "$REPO_ROOT/.opencode/commands" ]]; then
        log_warn "未发现 $REPO_ROOT/.opencode/commands；WebUI 将缺少项目级 commands"
    fi

    log_info "repo_root=$REPO_ROOT"
    log_info "service_unit=$SERVICE_UNIT"
    log_info "listen_target=${SERVER_HOST}:${PORT}"
    step_end 0
}

service_is_active() {
    systemctl --user is-active --quiet "$SERVICE_UNIT"
}

list_web_pids() {
    pgrep -f "opencode web" || true
}

port_is_listening() {
    ss -tln 2>/dev/null | grep -q "${SERVER_HOST}:${PORT} "
}

stop_legacy_web_processes() {
    step_begin "清理遗留 opencode web 进程"

    systemctl --user stop "$SERVICE_UNIT" >/dev/null 2>&1 || true

    local old_pids=""
    local remain=""
    local waited=0

    old_pids="$(list_web_pids)"
    if [[ -z "$old_pids" ]]; then
        log_info "未发现遗留 opencode web 进程"
        step_end 0
        return
    fi

    log_info "发现遗留 opencode web 进程: $old_pids"
    kill $old_pids 2>/dev/null || true

    while [[ $waited -lt 25 ]]; do
        sleep 0.2
        waited=$((waited + 1))
        remain="$(list_web_pids)"
        [[ -z "$remain" ]] && break
    done

    remain="$(list_web_pids)"
    if [[ -n "$remain" ]]; then
        log_warn "SIGTERM 后仍存活，发送 SIGKILL: $remain"
        kill -9 $remain 2>/dev/null || true
        sleep 0.5
    fi

    remain="$(list_web_pids)"
    if [[ -n "$remain" ]]; then
        step_end 1
        log_error "仍有 opencode web 进程未退出: $remain"
        harness_exit 1
    fi

    log_info "遗留 opencode web 进程已清理"
    step_end 0
}

write_systemd_service() {
    step_begin "生成/更新 systemd user service"

    mkdir -p "$SYSTEMD_USER_DIR" || {
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "mkdir -p $SYSTEMD_USER_DIR" "$rc"
    }

    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=OpenCode Web UI Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.config/opencode/server.env
WorkingDirectory=$REPO_ROOT
ExecStart=$OPENCODE_BIN web --hostname $SERVER_HOST --port $PORT
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload >/dev/null 2>&1 || {
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "systemctl --user daemon-reload" "$rc"
    }

    log_info "service_file=$SERVICE_FILE"
    step_end 0
}

restart_systemd_service() {
    step_begin "启动/重启 WSL2 内 opencode web service"

    systemctl --user enable "$SERVICE_UNIT" >/dev/null 2>&1 || {
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "systemctl --user enable $SERVICE_UNIT" "$rc"
    }

    systemctl --user restart "$SERVICE_UNIT" >/dev/null 2>&1 || {
        local rc=$?
        step_end "$rc"
        on_err "${BASH_LINENO[0]}" "systemctl --user restart $SERVICE_UNIT" "$rc"
    }

    if ! service_is_active; then
        step_end 1
        log_error "$SERVICE_UNIT 未处于 active 状态"
        log_error "查看日志: journalctl --user -u $SERVICE_UNIT --no-pager -n 100"
        harness_exit 1
    fi

    step_end 0
}

check_local_service_ready() {
    step_begin "检查 WSL2 本地 service 与监听端口"

    local failed=0
    local service_state=""
    local waited=0

    while [[ $waited -lt 25 ]]; do
        service_state="$(systemctl --user is-active "$SERVICE_UNIT" 2>/dev/null || true)"
        if [[ "$service_state" == "active" ]] && port_is_listening; then
            break
        fi
        sleep 0.2
        waited=$((waited + 1))
    done

    service_state="$(systemctl --user is-active "$SERVICE_UNIT" 2>/dev/null || true)"
    if [[ "$service_state" != "active" ]]; then
        log_error "$SERVICE_UNIT 当前不是 active（state=${service_state:-unknown}）"
        log_error "查看日志: journalctl --user -u $SERVICE_UNIT --no-pager -n 100"
        failed=1
    else
        log_info "$SERVICE_UNIT 处于 active 状态"
    fi

    if ! port_is_listening; then
        log_error "未检测到 ${SERVER_HOST}:${PORT} 监听"
        log_error "查看状态: systemctl --user status $SERVICE_UNIT --no-pager"
        failed=1
    else
        log_info "已检测到 ${SERVER_HOST}:${PORT} 监听"
    fi

    if [[ $failed -ne 0 ]]; then
        step_end 1
        harness_exit 1
    fi

    step_end 0
}

build_tailscale_powershell() {
    local mode="$1"

    cat <<EOF
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
\$ErrorActionPreference = 'Stop'

\$defaultPath = 'C:\\Program Files\\Tailscale\\tailscale.exe'
if (Test-Path \$defaultPath) {
  \$ts = \$defaultPath
} else {
  \$cmd = Get-Command tailscale.exe -ErrorAction SilentlyContinue
  if (\$null -eq \$cmd) {
    throw 'tailscale.exe not found. Install Tailscale on Windows and login first.'
  }
  \$ts = \$cmd.Source
}

& \$ts status | Out-Null
EOF

    if [[ "$mode" == "configure" ]]; then
        cat <<EOF
& \$ts serve --bg --https=$TAILSCALE_SERVE_PORT http://localhost:$PORT | Out-Null
EOF
    fi

    cat <<'EOF'
$serveStatus = (& $ts serve status | Out-String).TrimEnd()
$urlMatch = [regex]::Match($serveStatus, 'https://\S+')

Write-Output ('TAILSCALE_EXE=' + $ts)
if ($urlMatch.Success) {
  Write-Output ('SERVE_URL=' + $urlMatch.Value)
}
Write-Output 'SERVE_STATUS_BEGIN'
Write-Output $serveStatus
Write-Output 'SERVE_STATUS_END'
EOF
}

parse_tailscale_output() {
    local output="$1"
    local in_status=0
    local line=""

    TAILSCALE_EXE=""
    SERVE_URL=""
    SERVE_STATUS_OUTPUT=""

    while IFS= read -r line; do
        case "$line" in
            TAILSCALE_EXE=*)
                TAILSCALE_EXE="${line#TAILSCALE_EXE=}"
                ;;
            SERVE_URL=*)
                SERVE_URL="${line#SERVE_URL=}"
                ;;
            SERVE_STATUS_BEGIN)
                in_status=1
                ;;
            SERVE_STATUS_END)
                in_status=0
                ;;
            *)
                if [[ $in_status -eq 1 ]]; then
                    if [[ -n "$SERVE_STATUS_OUTPUT" ]]; then
                        SERVE_STATUS_OUTPUT+=$'\n'
                    fi
                    SERVE_STATUS_OUTPUT+="$line"
                fi
                ;;
        esac
    done <<< "$output"
}

print_multiline_info() {
    local prefix="$1"
    local text="$2"
    local line=""

    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        log_info "$prefix$line"
    done <<< "$text"
}

configure_or_check_tailscale_serve() {
    local mode="$1"
    local title=""
    local ps_script=""
    local ps_output=""
    local exit_code=1

    if [[ "$mode" == "configure" ]]; then
        title="配置 Windows Tailscale Serve"
    else
        title="检查 Windows Tailscale Serve"
    fi

    step_begin "$title"

    ps_script="$(build_tailscale_powershell "$mode")"
    ps_output="$(powershell.exe -NoProfile -Command "$ps_script" 2>&1 | tr -d '\r')" || {
        if [[ "$ps_output" == *"tailscale.exe not found"* || "$ps_output" == *"login first"* || "$ps_output" == *"Logged out"* ]]; then
            exit_code=3
        fi
        step_end "$exit_code"
        log_error "Windows Tailscale 检查/配置失败"
        [[ -n "$ps_output" ]] && print_multiline_info "powershell: " "$ps_output"
        if [[ $exit_code -eq 3 ]]; then
            log_error "请确认 Windows 已安装 Tailscale，并已登录同一 tailnet"
        fi
        harness_exit "$exit_code"
    }

    parse_tailscale_output "$ps_output"

    [[ -n "$TAILSCALE_EXE" ]] || {
        step_end 1
        log_error "未能解析 tailscale.exe 路径"
        harness_exit 1
    }

    [[ -n "$SERVE_STATUS_OUTPUT" ]] && print_multiline_info "serve: " "$SERVE_STATUS_OUTPUT"

    [[ -n "$SERVE_URL" ]] || {
        step_end 1
        log_error "未从 tailscale serve status 中解析到 HTTPS URL"
        log_error "请检查上方 serve 状态输出"
        harness_exit 1
    }

    log_info "tailscale_exe=$TAILSCALE_EXE"
    log_info "serve_url=$SERVE_URL"
    step_end 0
}

emit_summary() {
    log_result "OpenCode WebUI + Tailscale 已就绪" \
        "service=$SERVICE_UNIT" \
        "listen=${SERVER_HOST}:${PORT}" \
        "auth_user=$OPENCODE_SERVER_USERNAME" \
        "serve_url=$SERVE_URL" \
        "project_root=$REPO_ROOT" \
        "service_file=$SERVICE_FILE"

    log_info "手机访问: $SERVE_URL"
    log_info "认证账号: $OPENCODE_SERVER_USERNAME"
    log_info "WSL 服务日志: journalctl --user -u $SERVICE_UNIT --no-pager -n 100"
    log_info "Windows Serve 状态: powershell.exe -NoProfile -Command \"& '$TAILSCALE_EXE' serve status\""
}

parse_args "$@"
run_prechecks
load_server_env

if [[ $STATUS_ONLY -eq 1 ]]; then
check_local_service_ready
configure_or_check_tailscale_serve "status"
emit_summary
harness_exit 0
fi

stop_legacy_web_processes

if [[ $RESTART_SERVE_ONLY -eq 1 ]]; then
    check_local_service_ready
    configure_or_check_tailscale_serve "configure"
    emit_summary
    harness_exit 0
fi

write_systemd_service
restart_systemd_service
check_local_service_ready
configure_or_check_tailscale_serve "configure"
emit_summary
harness_exit 0

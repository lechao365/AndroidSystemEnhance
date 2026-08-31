#!/bin/bash
# ============================================================================
# start_opencode_server_tailscale.sh — Windows Tailscale 侧实现（start-opencode-server.sh 拆出件）
# 含: PowerShell 脚本组装、serve 输出解析、多行信息输出、配置/检查 Tailscale serve。
# 位于 lib/shell/ 下，由入口脚本 source 加载；
# 函数在调用时解析全局配置（TAILSCALE_SERVE_PORT/PORT 等）。
# 安全约束见 harness/reference/remote-access-reference.md（只用 serve，禁用 funnel）。
# ============================================================================

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
        lc_exit "$exit_code"
    }

    parse_tailscale_output "$ps_output"

    [[ -n "$TAILSCALE_EXE" ]] || {
        step_end 1
        log_error "未能解析 tailscale.exe 路径"
        lc_exit 1
    }

    [[ -n "$SERVE_STATUS_OUTPUT" ]] && print_multiline_info "serve: " "$SERVE_STATUS_OUTPUT"

    [[ -n "$SERVE_URL" ]] || {
        step_end 1
        log_error "未从 tailscale serve status 中解析到 HTTPS URL"
        log_error "请检查上方 serve 状态输出"
        lc_exit 1
    }

    log_info "tailscale_exe=$TAILSCALE_EXE"
    log_info "serve_url=$SERVE_URL"
    step_end 0
}

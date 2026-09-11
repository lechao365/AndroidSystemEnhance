#!/bin/bash
# ============================================================================
# start_opencode_server_systemd.sh — WSL2/systemd 侧实现（start-opencode-server.sh 拆出件）
# 含: 前置检查、server.env 加载、service 启停与就绪检查、结果汇总。
# 位于 lib/shell/ 下，由入口脚本 source 加载；
# 函数在调用时解析入口设置的全局配置（TARGET_ROOT/PORT/SERVICE_UNIT 等）。
# 工程根定位由入口脚本 locate_project_root 承担（AGENTS.md 锚点）。
# ============================================================================

require_command() {
    local cmd="$1"
    local hint="$2"

    command -v "$cmd" >/dev/null 2>&1 || {
        log_error "$hint"
        lc_exit 3
    }
}

load_server_env() {
    step_begin "加载 server.env 凭据"

    [[ -f "$SERVER_ENV_FILE" ]] || {
        step_end 1
        log_error "未找到 $SERVER_ENV_FILE"
        log_error "请先配置 OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD"
        lc_exit 3
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
        lc_exit 3
    }

    [[ -n "$OPENCODE_SERVER_PASSWORD" ]] || {
        step_end 1
        log_error "server.env 缺少 OPENCODE_SERVER_PASSWORD"
        lc_exit 3
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
        lc_exit 3
    }

    log_info "target_root=$TARGET_ROOT"
    log_info "service_unit=$SERVICE_UNIT"
    log_info "listen_target=${SERVER_HOST}:${PORT}"
    step_end 0
}

service_is_active() {
    systemctl --user is-active --quiet "$SERVICE_UNIT"
}

list_web_pids() {
    # pgrep: 0=有匹配, 1=无匹配（正常）, 其他=异常；显式区分，不吞错
    local pids=""
    pids="$(pgrep -f "opencode web" 2>/dev/null)" || {
        local rc=$?
        if [[ $rc -ne 1 ]]; then
            log_warn "pgrep 查询异常（rc=$rc），按无遗留进程处理"
        fi
        pids=""
    }
    printf '%s\n' "$pids"
}

port_is_listening() {
    ss -tln 2>/dev/null | grep -q "${SERVER_HOST}:${PORT} "
}

stop_legacy_web_processes() {
    step_begin "清理遗留 opencode web 进程"

    # 显式处理 stop：service 未运行（stop 非零）属正常；运行中 stop 失败则警告
    if ! systemctl --user stop "$SERVICE_UNIT" >/dev/null 2>&1; then
        if service_is_active; then
            log_warn "systemctl --user stop $SERVICE_UNIT 失败（service 仍 active）"
        else
            log_info "service 未运行，跳过 stop"
        fi
    fi

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
    if ! kill $old_pids 2>/dev/null; then
        log_warn "SIGTERM 部分失败（进程可能已退出），继续等待"
    fi

    while [[ $waited -lt 25 ]]; do
        sleep 0.2
        waited=$((waited + 1))
        remain="$(list_web_pids)"
        [[ -z "$remain" ]] && break
    done

    remain="$(list_web_pids)"
    if [[ -n "$remain" ]]; then
        log_warn "SIGTERM 后仍存活，发送 SIGKILL: $remain"
        if ! kill -9 $remain 2>/dev/null; then
            log_warn "SIGKILL 部分失败（进程可能已退出）"
        fi
        sleep 0.5
    fi

    remain="$(list_web_pids)"
    if [[ -n "$remain" ]]; then
        step_end 1
        log_error "仍有 opencode web 进程未退出: $remain"
        lc_exit 1
    fi

    log_info "遗留 opencode web 进程已清理"
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
        lc_exit 1
    fi

    step_end 0
}

check_local_service_ready() {
    step_begin "检查 WSL2 本地 service 与监听端口"

    local failed=0
    local service_state=""
    local waited=0

    while [[ $waited -lt 25 ]]; do
        # is-active 非 active 时返回非零并输出状态字符串，显式兜底为 unknown
        service_state="$(systemctl --user is-active "$SERVICE_UNIT" 2>/dev/null)"
        service_state="${service_state:-unknown}"
        if [[ "$service_state" == "active" ]] && port_is_listening; then
            break
        fi
        sleep 0.2
        waited=$((waited + 1))
    done

    service_state="$(systemctl --user is-active "$SERVICE_UNIT" 2>/dev/null)"
    service_state="${service_state:-unknown}"
    if [[ "$service_state" != "active" ]]; then
        log_error "$SERVICE_UNIT 当前不是 active（state=${service_state}）"
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
        lc_exit 1
    fi

    step_end 0
}

emit_summary() {
    log_result "OpenCode WebUI + Tailscale 已就绪" \
        "service=$SERVICE_UNIT" \
        "listen=${SERVER_HOST}:${PORT}" \
        "auth_user=$OPENCODE_SERVER_USERNAME" \
        "serve_url=$SERVE_URL" \
        "project_root=$TARGET_ROOT" \
        "service_file=$SERVICE_FILE"

    log_info "手机访问: $SERVE_URL"
    log_info "认证账号: $OPENCODE_SERVER_USERNAME"
    log_info "WSL 服务日志: journalctl --user -u $SERVICE_UNIT --no-pager -n 100"
    log_info "Windows Serve 状态: powershell.exe -NoProfile -Command \"& '$TAILSCALE_EXE' serve status\""
}

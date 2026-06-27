# OpenCode Tailscale 一键启动脚本实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `engineering/harness/scripts/start-opencode-server.sh` 重构为一键入口：在 WSL2 内托管启动项目级 `opencode web`，并在 Windows 宿主上配置 `tailscale serve`，最终可直接在手机上通过 Tailscale HTTPS + `server.env` 账号密码访问 WebUI。

**Architecture:** 脚本在 WSL2 中执行，使用 harness bootstrap / observability API 管理日志与退出码；WSL 侧通过 systemd user service 托管 `opencode web --hostname 127.0.0.1 --port <port>`，以项目根为 `WorkingDirectory` 载入 `.opencode/commands`；Windows 侧通过 `powershell.exe` 调用 `tailscale.exe serve --bg --https=443 http://localhost:<port>` 暴露 tailnet-only HTTPS 入口，并解析 `serve status` 输出最终 URL。

**Tech Stack:** Bash、systemd user service、WSL2、Windows PowerShell、Tailscale Serve、OpenCode、harness bootstrap/path util。

---

### Task 1: 先补脚本行为级测试基线与验证命令

**Files:**
- Modify: `engineering/harness/scripts/validate_harness_scripts.sh`
- Test: `engineering/harness/scripts/start-opencode-server.sh`
- Test: `engineering/harness/scripts/validate_harness_scripts.sh`

- [ ] **Step 1: 定义这次重构的最小可验证行为**

本次不新增独立测试框架，采用现有 harness validator + 脚本实际运行验证作为回归基线。目标行为：

1. `start-opencode-server.sh` 继续满足 bootstrap / `harness_init` / `harness_exit` / 无裸 `/tmp/` / 无私有 API 依赖。
2. 脚本无参执行时，默认走：
   - 校验 `~/.config/opencode/server.env`
   - 生成 systemd user service
   - `systemctl --user enable --now`
   - 调用 Windows `tailscale serve`
   - 输出 HTTPS URL
3. `--status-only` 只检查，不重启服务。
4. `--restart-serve-only` 仅重配 Windows Serve。

- [ ] **Step 2: 先运行现有 validator，确认旧脚本当前基线**

Run: `bash engineering/harness/scripts/validate_harness_scripts.sh`
Expected: PASS（退出码 0）或仅已有已知告警；确保重构后不能引入新的脚本规范告警。

- [ ] **Step 3: 记录本次实际功能验证命令**

后续实现完成后必须至少执行：

```bash
bash engineering/harness/scripts/validate_harness_scripts.sh
bash engineering/harness/scripts/start-opencode-server.sh --status-only
bash engineering/harness/scripts/start-opencode-server.sh
systemctl --user status opencode-web.service --no-pager
ss -tlnp | grep 4096
powershell.exe -NoProfile -Command "& 'C:\Program Files\Tailscale\tailscale.exe' serve status"
```

Expected:
- validator 退出码为 0
- WSL 内监听 `127.0.0.1:4096`
- `serve status` 显示 `https://...ts.net` 且代理到 `http://localhost:4096`

### Task 2: 重构 `start-opencode-server.sh` 为 WSL systemd + Windows Tailscale Serve 一键入口

**Files:**
- Modify: `engineering/harness/scripts/start-opencode-server.sh`
- Modify: `engineering/harness/config/harness-paths.conf`（仅当新增可复用路径 KEY 必要时）
- Test: `engineering/harness/scripts/start-opencode-server.sh`

- [ ] **Step 1: 写出会失败的接口设计检查清单**

目标 CLI：

```bash
bash engineering/harness/scripts/start-opencode-server.sh
bash engineering/harness/scripts/start-opencode-server.sh --status-only
bash engineering/harness/scripts/start-opencode-server.sh --restart-serve-only
bash engineering/harness/scripts/start-opencode-server.sh --port 4097
bash engineering/harness/scripts/start-opencode-server.sh --service-name custom-opencode-web
```

若当前脚本仍是旧版行为，则以下需求无法满足，视作“失败测试”：
- 不能创建/重载 systemd user service
- 不能调用 Windows `tailscale.exe serve`
- 不能输出 tailnet URL
- 不能复用 `server.env`

- [ ] **Step 2: 实现最小参数解析与前置检查**

实现点：
- 默认 `PORT=4096`、`SERVICE_NAME=opencode-web`
- 支持 `--status-only`、`--restart-serve-only`、`--port`、`--service-name`
- 校验命令：`opencode`、`systemctl`、`powershell.exe`
- 校验文件：`$HOME/.config/opencode/server.env`
- 校验环境变量：`OPENCODE_SERVER_USERNAME`、`OPENCODE_SERVER_PASSWORD`
- 保持 observability：每个阶段 `step_begin/step_end`

- [ ] **Step 3: 实现 systemd user service 生成逻辑**

Service 文件目标路径：
`$HOME/.config/systemd/user/<service-name>.service`

核心内容：

```ini
[Unit]
Description=OpenCode Web UI Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.config/opencode/server.env
WorkingDirectory=<REPO_ROOT>
ExecStart=<which opencode> web --hostname 127.0.0.1 --port <PORT>
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

要求：
- 脚本动态写入 `WorkingDirectory=$REPO_ROOT`
- `ExecStart` 使用 `command -v opencode` 结果，避免硬编码 node 版本路径
- 写入前确保目录存在
- 生成完成后 `systemctl --user daemon-reload`

- [ ] **Step 4: 实现 WSL 服务启动/状态检查逻辑**

命令序列：

```bash
systemctl --user enable --now "$SERVICE_NAME.service"
systemctl --user restart "$SERVICE_NAME.service"
systemctl --user is-active "$SERVICE_NAME.service"
ss -tlnp | grep "127.0.0.1:${PORT}"
```

行为要求：
- 默认执行完整重启
- `--status-only` 下仅检查 active/listen 状态
- 若未监听则输出 `journalctl --user -u "$SERVICE_NAME.service" --no-pager -n 50` 指引

- [ ] **Step 5: 实现 Windows Tailscale Serve 配置逻辑**

使用 `powershell.exe -NoProfile -Command` 调用：

```powershell
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
& $ts status
& $ts serve --bg --https=443 http://localhost:<PORT>
& $ts serve status
```

要求：
- 若默认路径不存在，再回退 `Get-Command tailscale.exe`
- 明确拒绝 funnel，仅配置 serve
- `--restart-serve-only` 时跳过 WSL service 重启，仅重配 serve
- 脚本解析 `serve status` 输出中的 `https://...` URL

- [ ] **Step 6: 实现最终结构化结果输出**

成功时 `log_result` 至少包含：

```text
service=<SERVICE_NAME>.service
listen=127.0.0.1:<PORT>
auth_user=<OPENCODE_SERVER_USERNAME>
serve_url=https://...ts.net
project_root=<REPO_ROOT>
service_file=<path>
```

并补充排障提示：
- `journalctl --user -u <service> --no-pager -n 100`
- `tailscale serve status`

### Task 3: 更新 README 使用示例与运维说明

**Files:**
- Modify: `engineering/harness/scripts/README.md`
- Test: `engineering/harness/scripts/README.md`

- [ ] **Step 1: 更新脚本说明表中的用途描述**

将 `start-opencode-server.sh` 描述改为强调：
- WSL2 内托管 `opencode web`
- 自动复用 `~/.config/opencode/server.env`
- 自动配置 Windows `tailscale serve`
- 输出手机可访问的 HTTPS WebUI URL

- [ ] **Step 2: 在 README 补充最小使用示例**

加入示例命令：

```bash
bash engineering/harness/scripts/start-opencode-server.sh
bash engineering/harness/scripts/start-opencode-server.sh --status-only
bash engineering/harness/scripts/start-opencode-server.sh --restart-serve-only
```

并注明前提：
- WSL2 已启用 systemd
- Windows 已安装并登录 Tailscale
- `~/.config/opencode/server.env` 已配置

### Task 4: 运行验证并修正问题直到全绿

**Files:**
- Test: `engineering/harness/scripts/start-opencode-server.sh`
- Test: `engineering/harness/scripts/validate_harness_scripts.sh`
- Test: `engineering/harness/scripts/README.md`

- [ ] **Step 1: 运行脚本规范校验**

Run: `bash engineering/harness/scripts/validate_harness_scripts.sh`
Expected: 退出码 0

- [ ] **Step 2: 运行状态检查模式**

Run: `bash engineering/harness/scripts/start-opencode-server.sh --status-only`
Expected: 正确输出当前 service / 端口 / serve 状态；若服务未启动也应给出可理解提示，而不是脚本异常退出。

- [ ] **Step 3: 运行完整一键启动**

Run: `bash engineering/harness/scripts/start-opencode-server.sh`
Expected:
- systemd user service active
- `ss -tlnp` 显示 `127.0.0.1:4096`
- `serve status` 返回 `https://...ts.net`

- [ ] **Step 4: 手动 spot-check 关键链路**

Run:

```bash
systemctl --user status opencode-web.service --no-pager
powershell.exe -NoProfile -Command "& 'C:\Program Files\Tailscale\tailscale.exe' serve status"
```

Expected:
- service 活跃
- `serve` 代理到 `http://localhost:4096`

- [ ] **Step 5: 如验证失败，最小修复后重跑全部验证**

要求：每次修复后至少重跑：

```bash
bash engineering/harness/scripts/validate_harness_scripts.sh && \
bash engineering/harness/scripts/start-opencode-server.sh && \
systemctl --user status opencode-web.service --no-pager
```

直到全部通过。

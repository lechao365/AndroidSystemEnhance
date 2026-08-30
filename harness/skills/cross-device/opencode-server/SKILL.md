---
name: opencode-server
description: 一键拉起 OpenCode WebUI（WSL2 + Windows Tailscale）。把 opencode web 托管为 systemd user service、配置 tailscale serve HTTPS 反向代理并输出手机可访问的 tailnet URL；为跨设备 emit/apply 协作提供 WebUI 入口。
no_commit: true
stages:
  - preflight: "前置检查 + 加载凭据"
  - cleanup: "清理遗留进程"
  - start: "生成/更新 systemd unit + 启动 service"
  - serve: "配置 tailscale serve"
  - report: "输出汇总"
---

# opencode-server

在 WSL2 内以 systemd user service 托管 `opencode web`，并在 Windows 宿主上配置
`tailscale serve` HTTPS -> localhost:PORT，输出手机可访问的 tailnet HTTPS URL
（WebUI 认证复用 server.env 凭据）。

**核心语义**：脚本做机械工作（生成/更新 systemd unit、清理遗留进程、配置
tailscale serve），AI 读取脚本输出报告进展。目标 workspace 固定为本项目工程根
（AGENTS.md 锚点自动定位）。

**定位**：跨设备协作基础设施——apply 设备（本地 WSL2）经本 skill 拉起 WebUI，
emit 侧（远端）经 tailnet 从浏览器/手机访问，衔接 `/cross-device-emit` 与
`/cross-device-apply` 工作流（详见
[harness/skills/cross-device/docs/cdp-contract.md](../docs/cdp-contract.md)）。

## 触发条件（Trigger）

- 用户表达"拉起 OpenCode WebUI"/"start opencode server"/"手机访问 opencode"意图
- 需要重新配置 WSL2 opencode web 的 systemd 托管与 Tailscale 访问

## 前置条件（Preconditions）

1. 当前环境为 WSL2/Linux，且已启用 systemd（`systemctl --user` 可用）
2. WSL2 内已安装 `opencode-ai`，Windows 宿主已安装并登录 Tailscale
3. `server.env` 已配置 `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD`
   （默认 `~/.config/opencode/server.env`）

## 输入（Inputs）

| 参数 | 说明 |
|------|------|
| `--port <port>` | WebUI 监听端口，默认 4096 |
| `--service-name <name>` | systemd user service 名称，默认 opencode-web |
| `--status-only` | 仅检查当前 WSL service / 监听端口 / tailscale serve 状态 |
| `--restart-serve-only` | 仅重配 Windows tailscale serve；不重启 WSL service |
| `-h, --help` | 显示帮助（无日志/汇总副作用） |

## 工作流

1. **前置检查**：脚本校验 opencode/systemctl/ss/powershell.exe 可用，`systemctl --user` 可执行
2. **加载凭据**：读取 `server.env`（`ENV_OPENCODE_SERVER_ENV_FILE` 可覆盖路径）
3. **清理遗留进程**：停止旧 service，SIGTERM 后残留进程 SIGKILL（失败显式警告）
4. **生成/更新 systemd unit**：原子写（同目录临时文件 + mv），`EnvironmentFile` 指向
   `$SERVER_ENV_FILE` 实际路径，`WorkingDirectory` 指向工程根
5. **启动 service + 就绪检查**：等待 active 且端口监听
6. **配置 Tailscale serve**：Windows 宿主 `tailscale serve --https=<port>` -> `http://localhost:PORT`
7. **输出汇总**：service/listen/auth_user/serve_url/project_root/service_file

`--status-only` 仅执行 2/5/6/7；`--restart-serve-only` 跳过 3/4（service 重启）。

## 输出（Outputs / artifacts）

| 场景 | 输出 |
|------|------|
| 配置完成 | 运行汇总（service、listen、auth_user、serve_url）+ 手机访问 URL |
| 仅状态检查 | 当前 service 状态、监听端口、tailscale serve 状态 |

日志按 skill 内聚落盘 harness/log/opencode-server/（gitignore 工作态，不入库）。

## 失败恢复（Failure / recovery）

| 场景 | 退出码 | 处理 |
|------|--------|------|
| service 未 active / 监听未就绪 / 遗留进程未清理 / tailscale 解析失败 | 1 | 查看 journalctl 与 serve status，修复后重试 |
| 参数/环境错误（未知参数、server.env 缺失、命令未找到、systemctl 不可用、工程根定位失败） | 3 | 检查参数与环境后重试 |

## 配置（环境变量可覆盖）

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `ENV_OPENCODE_SERVER_PORT` | `4096` | WebUI 监听端口 |
| `ENV_OPENCODE_SERVER_HOST` | `127.0.0.1` | WebUI 监听地址 |
| `ENV_OPENCODE_SERVER_NAME` | `opencode-web` | systemd service 名 |
| `ENV_OPENCODE_SERVER_ENV_FILE` | `~/.config/opencode/server.env` | 凭据文件路径 |
| `ENV_SYSTEMD_USER_DIR` | `~/.config/systemd/user` | systemd user unit 目录 |
| `ENV_TAILSCALE_SERVE_PORT` | `443` | Tailscale serve HTTPS 端口 |
| `ENV_OPENCODE_SERVER_PROJECT_ROOT` | AGENTS.md 锚点自动定位 | WebUI 打开的工程根 |

## 退出码

| 退出码 | 含义 | 下一步 |
|--------|------|--------|
| 0 | 成功（WebUI + Tailscale 已就绪） | 正常 |
| 1 | 通用失败（service 未 active / 监听端口未就绪 / 遗留进程未清理 / tailscale 解析失败） | 查看日志与状态后重试 |
| 3 | 参数/环境错误（未知参数 / server.env 缺失 / 命令未找到 / systemctl 不可用 / 工程根定位失败 / 参数含非法字符或不能同时使用） | 检查参数与环境 |

## 增量验证（Incremental verification）

- 校验器：`harness/skills/cross-device/opencode-server/validate_opencode_server.py`
- 运行：`python3 harness/skills/cross-device/opencode-server/validate_opencode_server.py`
- 本 skill 不直接修改代码文件（纯运维：systemd unit + tailscale serve），其验证内嵌在脚本与校验器中

## 相关规则（Related policy IDs）

- `RMT-001` ~ `RMT-008`：远程访问安全约束（只用 serve 禁用 funnel、server.env 600 权限、
  loopback 绑定、tag/ACL 三阶段顺序等），见
  [harness/reference/remote-access-reference.md](../../../reference/remote-access-reference.md)
- `CDP-001`：本 skill 为 cross-device emit/apply 链路提供 WebUI 入口，契约见
  [harness/skills/cross-device/docs/cdp-contract.md](../docs/cdp-contract.md)

## 不做的事（YAGNI）

- 不做多 workspace 管理（目标固定为本项目工程根）
- 不做凭据管理（server.env 由用户自行维护）
- 不做 Windows 侧安装/登录 Tailscale

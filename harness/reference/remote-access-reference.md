# 远程访问参考（Tailscale + Serve）

> **规则 ID**: `RMT-001` ~ `RMT-008`
> **适用范围**: 涉及跨网络（不限同一 WiFi）从移动端安全访问 WSL2 中的 opencode WebUI 时，AI 必须参考本文档。
> **参考来源**: 由远程访问教程重构而来（选型对比压缩为选型理由，保留纵深防御与全部硬性约束）。
>
> 前置：WSL2 **mirrored 网络模式**（localhost 与 Windows 宿主互通）+ systemd 已启用；opencode 全局安装（`npm install -g opencode-ai`）。

---

## 1. 选型与架构

**为什么用 Tailscale + Serve**：同时满足跨网络、端到端加密、受信任证书（浏览器无警告）、零公网 IP、零端口转发，且与 WSL2 mirrored 模式天然契合。

```
手机浏览器
   │ HTTPS（Tailscale 自动签发受信任证书）
   ▼
desktop-ra5vtnu.tailnet.ts.net:443   ← Tailscale Serve 终止 TLS
   │ WireGuard 加密（tailnet 内）
   ▼
Windows Tailscale 客户端
   │ mirrored 模式 localhost 互通
   ▼
WSL2 127.0.0.1:4096  (opencode web, 仅监听 loopback)
```

攻击面收窄到：只有「你的手机 + tailnet 凭证 + opencode 密码」三者齐全才能进入。

---

## 2. 纵深防御（7 层）

| 层 | 防护目标 | 措施 | 状态 |
|----|----------|------|------|
| L0 账号 | 防 Tailscale 账号被盗 | OAuth 登录 + 2FA + Device Approval | 必选 |
| L1 接入锁 | 防账号被盗后私加设备 | Tailnet Lock（本地密钥签名新节点） | 可选 |
| L2 ACL | 防 tailnet 内横向移动 | 最小权限 ACL：仅 `tag:phone` → `tag:win-host:443` | 必选 |
| L3 暴露范围 | 防公网扫描/爆破 | 只用 `tailscale serve`，**绝不用 `tailscale funnel`** | 必选 |
| L4 传输加密 | 防窃听/中间人 | WireGuard + 受信任 HTTPS 证书 | 必选（内置） |
| L5 应用认证 | 防未授权访问 WebUI | opencode 密码（28 位随机），600 权限文件，不进 history | 必选 |
| L6 服务托管 | 防崩溃后裸奔 | systemd user unit 托管，linger 常驻 | 必选 |

> **RMT-001**: **绝不能用 `tailscale funnel`**（会暴露到公网），只能 `tailscale serve`（仅 tailnet 可达）。

---

## 3. 操作步骤

### 3.0 Tailscale 账号加固

1. [login.tailscale.com](https://login.tailscale.com) 用 GitHub/Google OAuth 注册/登录。
2. 开启 2FA：Settings → Personal Settings → Account → Multi-factor authentication。
3. 开启 Device Approval：Settings → Device Management → 勾选 *Require device approval*。
4. 确认 HTTPS certificates 已启用（免费签发 Let's Encrypt 证书）。

### 3.1 WSL2 侧：生成密码并写环境文件

```bash
PW=$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-28)
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/server.env <<EOF
OPENCODE_SERVER_USERNAME=opencode
OPENCODE_SERVER_PASSWORD=${PW}
EOF
chmod 600 ~/.config/opencode/server.env
echo "请记录密码: ${PW}"
```

> **RMT-002**: `server.env` 权限必须 600，不进 shell history、不进 git。

### 3.2 WSL2 侧：systemd user unit

创建 `~/.config/systemd/user/opencode-web.service`：

```ini
[Unit]
Description=OpenCode Web (HTTPS via Tailscale Serve)
Documentation=https://opencode.ai/docs/web/
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.config/opencode/server.env
WorkingDirectory=%h
ExecStart=$(which opencode) web --hostname 127.0.0.1 --port 4096
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=default.target
```

> **RMT-003**: `ExecStart` 的 opencode 路径必须替换为本机实际路径（`which opencode` 查询）。
> **RMT-004**: **不要加 `ProtectHome=read-only`**，会阻止 opencode 写数据库（`~/.local/share/opencode/opencode.db`）。
> **RMT-005**: `--hostname 127.0.0.1` 只绑 loopback，局域网扫描也扫不到，仅 Tailscale Serve 能转发。

### 3.3 启用服务并开启 linger

```bash
systemctl --user daemon-reload
systemctl --user enable --now opencode-web.service
loginctl enable-linger <用户名>     # 让用户级服务在未登录时也常驻

systemctl --user status opencode-web.service
ss -tlnp | grep 4096               # 应看到 127.0.0.1:4096 LISTEN
```

### 3.4 Windows 侧：安装登录 Tailscale + 配置 Serve

```powershell
# 安装并登录（tailscale.com/download）
& 'C:\Program Files\Tailscale\tailscale.exe' status    # 确认本机在线

# 配置 Serve（管理员 PowerShell）
& 'C:\Program Files\Tailscale\tailscale.exe' serve --bg --https=443 http://localhost:4096

# 验证
& 'C:\Program Files\Tailscale\tailscale.exe' serve status
# 应输出: https://desktop-xxx.tailnet.ts.net (tailnet only) |-- / proxy http://localhost:4096
```

> `(tailnet only)` 表示仅 tailnet 内设备可达。若误用 funnel 会暴露到公网（RMT-001 禁止）。
>
> **RMT-006**: 首次配置可能弹出浏览器授权页，**直接在 Windows 管理员 PowerShell 执行**（从 WSL 调 `powershell.exe` 看不到弹窗会超时）。

### 3.5 移动端：安装 Tailscale 并加入

1. Android 从 Google Play/F-Droid、iOS 从 App Store 安装 Tailscale，登录同一账号。
2. **保活设置（Android 关键）**：App 设置开启 *Start on boot* + *Stay connected*；系统设置→应用→Tailscale→电池→「不限制」；最近任务加锁。
3. 确认 App 顶部显示 Connected。
4. Windows 侧 `tailscale.exe status` 确认手机在线。

### 3.6 Tailscale 后台：打 tag 与配置 ACL（必须三阶段顺序）

> **RMT-007**: 必须先在 ACL 里定义 `tagOwners`，自定义 tag 才会出现在设备的 tag 选择列表。**必须按 阶段A → B → C 顺序操作**，否则可能把设备锁出 tailnet。

**阶段 A：先在 ACL 加 `tagOwners`（acls 暂不动）**

打开 [admin/acls/file](https://login.tailscale.com/admin/acls/file)，在现有 JSON 最外层加 `tagOwners`，**保留当前 `acls` 不变**（防止把自己锁出去）：

```jsonc
{
  "tagOwners": {
    "tag:phone":    ["你的邮箱"],
    "tag:win-host": ["你的邮箱"]
  },
  "acls": [ /* 此处保留当前已有规则，先不改 */ ]
}
```

**阶段 B：回 machines 页给设备打 tag**

1. [admin/machines](https://login.tailscale.com/admin/machines) 找到 Windows 设备 → Edit ACL tags → 选中 `tag:win-host`。
2. 弹窗 "Assigning tags will change the ownership..." 是**正常安全提示**，点 Confirm 继续（因 ACL 的 `tagOwners` 已把该 tag 管理权授回 owner 账号，不会失去控制）。
3. 对手机同样操作，选中 `tag:phone`。

> **RMT-008**: 切忌在未配 `tagOwners` 的情况下硬确认 ownership 弹窗，否则可能把设备锁出 tailnet 无人能管理。

**阶段 C：两台设备都打完 tag 后，收紧 acls**

将编辑器内容整体替换为最小权限版：

```jsonc
{
  "tagOwners": {
    "tag:phone":    ["你的邮箱"],
    "tag:win-host": ["你的邮箱"]
  },
  "ssh": [],
  "acls": [
    { "action": "accept", "src": ["tag:phone"], "dst": ["tag:win-host:443"] }
  ]
}
```

> 效果：tailnet 内任何其他设备（未来新增的、被攻陷的）都打不开 443，横向移动归零。规则 1-2 分钟内全网下发。
>
> 可选增强：如需远程桌面，追加 `{ "action": "accept", "src": ["tag:phone"], "dst": ["tag:win-host:3389"] }`。

### 3.7 移动端访问

手机浏览器打开 `https://desktop-xxx.tailnet.ts.net`，Basic Auth 输入：
- 用户名：`opencode`
- 密码：`cat ~/.config/opencode/server.env` 查看

---

## 4. 验证方法

```bash
# 1. WSL 服务运行 + 仅监听 loopback
systemctl --user status opencode-web.service
ss -tlnp | grep 4096

# 2. WSL 本地认证（无凭证 401，有凭证 200）
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:4096/
curl -s -o /dev/null -w "%{http_code}\n" -u opencode:密码 http://127.0.0.1:4096/

# 3. Windows 经 mirrored 模式直达 WSL
curl.exe -s -o /dev/null -w "%{http_code}\n" -u opencode:密码 http://localhost:4096/

# 4. Tailscale Serve 配置正确
powershell.exe -NoProfile -Command "& 'C:\Program Files\Tailscale\tailscale.exe' serve status"

# 5. tailnet 设备在线 / 路由连通
powershell.exe -NoProfile -Command "& 'C:\Program Files\Tailscale\tailscale.exe' status"
powershell.exe -NoProfile -Command "& 'C:\Program Files\Tailscale\tailscale.exe' ping <手机IP>"

# 6. HTTPS 端到端
curl.exe -s -o /dev/null -w "%{http_code}\n" https://desktop-xxx.tailnet.ts.net/

# 7. ACL deny 生效（Windows→手机非 443 端口应失败）
powershell.exe -NoProfile -Command "Test-NetConnection -ComputerName <手机IP> -Port 22 -InformationLevel Quiet"  # 应返回 False
```

预期：无凭证恒 401、有凭证 200；ping 返回 pong；Test-NetConnection 对 22 端口返回 False。

---

## 5. 运维命令速查

```bash
systemctl --user start/stop/restart opencode-web.service
systemctl --user status opencode-web.service
journalctl --user -u opencode-web.service -f     # 实时日志

# 改密码
vim ~/.config/opencode/server.env                 # 改 OPENCODE_SERVER_PASSWORD
systemctl --user restart opencode-web.service

# 撤销/重配 Tailscale Serve（Windows PowerShell 管理员）
& 'C:\Program Files\Tailscale\tailscale.exe' serve reset
& 'C:\Program Files\Tailscale\tailscale.exe' serve --bg --https=443 http://localhost:4096

# 完全回滚
systemctl --user disable --now opencode-web.service
rm ~/.config/systemd/user/opencode-web.service ~/.config/opencode/server.env
& 'C:\Program Files\Tailscale\tailscale.exe' serve reset
```

---

## 6. 约束总览

| ID | 约束 | 违反后果 |
|----|------|---------|
| RMT-001 | 只用 `serve`，绝不用 `funnel` | WebUI 暴露公网 |
| RMT-002 | `server.env` 权限 600，不进 history/git | 密码泄露 |
| RMT-003 | ExecStart 用 `which opencode` 实际路径 | 服务启动失败 |
| RMT-004 | 禁止 `ProtectHome=read-only` | opencode.db 无法写入 |
| RMT-005 | `--hostname 127.0.0.1` 只绑 loopback | 暴露到局域网 |
| RMT-006 | Serve 配置在 Windows 管理员 PowerShell 执行 | 授权弹窗超时挂起 |
| RMT-007 | 打 tag 必须 阶段A→B→C 顺序 | 自定义 tag 不出现/锁出设备 |
| RMT-008 | 未配 tagOwners 前禁止硬确认 ownership | 设备锁出 tailnet |

---

## 7. 常见问题与排查

- **手机无法访问 `*.ts.net` 域名**：先用 IP 直连验证 `https://<tailnet IP>`。IP 通域名不通 → 确认 MagicDNS 开启、Android「Private DNS」关闭或自动（不能填第三方 DNS，否则劫持 `.ts.net` 解析）。
- **手机显示 offline**：Android 后台省电杀掉 Tailscale，按 3.5 设置电池不限制 + 后台加锁。
- **WSL 服务启动失败**：`journalctl --user -u opencode-web.service -e` 查日志，常见为 ExecStart 路径或 EnvironmentFile 路径错误。
- **443 端口被占用**：`Get-NetTCPConnection -LocalPort 443` 查占用（Tailscale 与 IIS/Skype 冲突），可改用 `--https=8443`。
- **ACL 保存后未生效**：规则下发需 1-2 分钟，`tailscale status --json` 检查设备 Tags 字段。
- **手机能连上但证书错误**：确认用 `https://*.tailnet.ts.net`（受信任证书），而非自签证书。

---

## 8. 安全提醒

- `~/.config/opencode/opencode.json` 中若硬编码明文 API key（如 airproxy/deepseek/visioncoder），opencode WebUI 的 `/config` 接口可能泄露给前端。建议将 key 迁到环境变量或 `~/.local/share/opencode/auth.json`。
- 本方案未启用 Tailnet Lock，依赖 2FA + Device Approval 兜底。长期建议启用。
- `server.env` 不要提交 git（在 `.gitignore` 排除）。

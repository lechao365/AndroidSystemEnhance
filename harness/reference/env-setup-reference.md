# WSL2 环境搭建参考

> **规则 ID**: `ENV-001` ~ `ENV-006`
> **适用范围**: 涉及 Windows + WSL2 宿主环境搭建、AOSP 编译前准备、ADB 调试工具接入时，AI 必须参考本文档。**禁止自行猜测 WSL2 配置或安装方式**。
> **参考来源**: 由 WSL2 环境搭建教程重构而来，去除教学性内容，保留可执行步骤与硬性约束。

---

## 1. 总体说明

**为什么用 WSL2**：Android 源码下载、AOSP 编译、内核编译都是 Linux 生态工具链（repo/git/make/clang），WSL2 提供完整 Linux 内核与文件系统，是 Windows 宿主上运行 Android 开发的最小可行方案。

**目录规划约定**：所有源码放在 Linux 文件系统（如 `~/workspace/aosp`），**禁止放在 `/mnt/c` 等 Windows 挂载盘**。原因：Windows 挂载盘在大量小文件场景下有严重 I/O 性能损失，AOSP 有数十万文件，会拖慢 repo sync 与编译。

---

## 2. 前置条件

| 项 | 要求 |
|----|------|
| 宿主机 | Windows 10 21H2+ 或 Windows 11 |
| 磁盘 | 至少 200GB 可用（建议 SSD） |
| 内存 | 16GB 起步，推荐 64GB 并在 WSL2 分配 32-48GB |
| 网络 | 可访问软件源；如访问 GitHub/Android 源受限需配置代理 |

---

## 3. 操作步骤

### 3.1 启用 WSL2 并安装 Ubuntu

> 为什么：`wsl --install` 会一次性启用 WSL 功能、安装虚拟化平台与默认发行版，避免手动勾选 Windows 功能。

在 **Windows PowerShell（管理员）** 执行：

```powershell
wsl --install
```

重启系统，首次进入 Ubuntu 时初始化用户名与密码，然后确认状态：

```bash
wsl -l -v
```

执行要求：
- 默认版本为 WSL2
- Ubuntu 可正常启动进入 shell

### 3.2 更新基础包并安装依赖

```bash
sudo apt update
sudo apt install -y git curl python3 python3-pip unzip zip adb openjdk-17-jdk repo
sudo apt autoremove -y
sudo apt clean
```

> 为什么用 JDK 17：AOSP Android 15 主线要求 JDK 17。`repo` 是 AOSP 源码仓库管理工具（多 git 仓库批量操作）。

确认核心工具可用：

```bash
python3 --version
git --version
adb version
java -version
repo --help
```

### 3.3 配置 git 身份与 SSH

最小 git 身份配置：

```bash
git config --global user.name "你的姓名"
git config --global user.email "你的邮箱"
git config --global core.autocrlf input
```

> 为什么 `core.autocrlf input`：避免 Windows/WSL 之间换行符（CRLF/LF）被改写，破坏源码仓库。

生成 SSH 密钥并添加 GitHub（避免每次 push 输密码）：

```bash
ssh-keygen -t ed25519 -C "你的GitHub邮箱"   # 一路回车
cat ~/.ssh/id_ed25519.pub                   # 复制公钥到 GitHub → Settings → SSH keys
ssh -T git@github.com                       # 验证，成功显示 Hi xxx!
git remote set-url origin git@github.com:用户名/仓库名.git   # HTTPS 改 SSH
```

> **ENV-001**: 如果在 Windows PowerShell 生成了密钥，WSL 默认无法访问，需复制到 WSL 并修正权限：
> ```bash
> cp /mnt/c/Users/你的用户名/.ssh/id_ed25519 ~/.ssh/
> cp /mnt/c/Users/你的用户名/.ssh/id_ed25519.pub ~/.ssh/
> chmod 600 ~/.ssh/id_ed25519
> chmod 644 ~/.ssh/id_ed25519.pub
> ```

> **ENV-002**: `git push` 报 `Failed to connect via 127.0.0.1` 说明配置了代理但代理未运行，执行：
> ```bash
> git config --global --unset http.proxy
> git config --global --unset https.proxy
> ```

### 3.4 规划源码目录并检查资源

```bash
mkdir -p ~/workspace/aosp
cd ~/workspace/aosp
df -h
free -h
swapon --show
```

> 为什么：AOSP 全量编译占用 200GB+ 磁盘、内存峰值可达几十 GB。`df -h`/`free -h`/`swapon --show` 用于判断资源是否满足后续构建。

### 3.5 调整 WSL2 内存与交换（大内存机器可选）

> 为什么：WSL2 默认只分配宿主机一半内存，AOSP 编译是内存密集型任务，可能不满足。通过 `.wslconfig` 手动分配。

创建/编辑 `C:\Users\<用户名>\.wslconfig`：

```ini
[wsl2]
memory=48GB
swap=0
localhostForwarding=true
```

> **ENV-003**: 修改 `.wslconfig` 后必须在 PowerShell 执行 `wsl --shutdown` 重启 WSL 使配置生效，重新进入后用 `free -h` 验证内存已调整。

### 3.6 配置代理（按需）

> 为什么：仅在访问软件源、代码仓或外部依赖失败时配置。国内访问 `android.googlesource.com` 通常需要代理。

```bash
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
```

验证连通性：

```bash
curl -I https://www.google.com
curl -I https://android.googlesource.com
```

### 3.7 验证环境可用性

```bash
python3 --version
git --version
adb version
java -version
mkdir -p ~/workspace/aosp && cd ~/workspace/aosp && pwd
df -h
free -h
adb devices   # 如需连接设备
```

---

## 4. 约束总览

| ID | 约束 | 违反后果 |
|----|------|---------|
| ENV-001 | Windows 生成的 SSH 密钥必须复制到 WSL 并设 600/644 权限 | SSH 认证失败或权限告警 |
| ENV-002 | 代理未运行时必须清除 git proxy 配置 | push/拉取报 Failed to connect |
| ENV-003 | `.wslconfig` 修改后必须 `wsl --shutdown` 重启 | 内存配置不生效 |
| ENV-004 | 源码目录必须放 Linux 文件系统，禁止 `/mnt/c` | repo sync/编译 I/O 极慢 |
| ENV-005 | 使用 JDK 17（AOSP 主线要求） | 编译报版本错误 |
| ENV-006 | `repo` 必须安装（AOSP 仓库管理依赖） | 无法初始化/同步源码 |

---

## 5. 常见问题与排查

- **WSL2 无法启动**：检查 Windows 功能中 WSL 与虚拟化平台是否启用，确认系统已重启，执行 `wsl -l -v` 与 `wsl --status` 查看状态。
- **`adb` 无法连接设备**：确认 USB 线、设备开发者选项、USB 调试授权，执行 `adb devices`；网络调试先确认主机与设备网络互通。
- **编译前磁盘不足**：`df -h` 检查，清理 `apt` 缓存与无用构建产物；不要将源码放 Windows 挂载盘。
- **代理导致下载失败**：`curl` 验证目标地址，检查代理变量是否生效；代理异常时取消代理后重试。

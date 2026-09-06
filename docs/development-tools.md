# 开发效率工具搭建指南（人类用户向）

> **用途**: 本文档面向**人类开发者**，介绍 AOSP / Linux Kernel 源码阅读与搜索环境的搭建（VS Code + OpenGrok）。
> 与 `harness/reference/` 下的 LLM 参考文档不同，本文档保留 IDE 配置与讲解，供人工开发使用。
> **来源**: 由开发调试环境教程重构而来。

---

## 1. VS Code + Remote-WSL 源码阅读环境

> 为什么用 Remote-WSL：VSCode 本体运行在 Windows 宿主机负责 UI 渲染，VSCode Server 运行在 WSL 中负责文件访问与语言服务，兼顾性能与体验，可直接阅读 AOSP 和 Linux Kernel 源码。

### 1.1 安装 VS Code 与 Remote-WSL

1. 访问 [VS Code 官网](https://code.visualstudio.com/) 下载安装 Windows 版本。
2. WSL 中确保 `code` 命令可用：

```bash
which code || echo "code 未在 WSL PATH 中"
export PATH="$PATH:/mnt/c/Users/<用户名>/AppData/Local/Programs/Microsoft VS Code/bin"
echo 'export PATH="$PATH:/mnt/c/Users/<用户名>/AppData/Local/Programs/Microsoft VS Code/bin"' >> ~/.bashrc
```

3. 安装 Remote-WSL 扩展：

```bash
code --install-extension ms-vscode-remote.remote-wsl
```

4. 打开 AOSP 源码（自动以 Remote-WSL 模式启动，左下角显示绿色 `WSL: Ubuntu`）：

```bash
code /home/lechao/workspace/aosp
```

### 1.2 安装代码阅读扩展

```bash
# C/C++（AOSP 和 Kernel 必需）
code --install-extension ms-vscode.cpptools
# Java（AOSP 框架层）
code --install-extension redhat.java
# 代码导航增强（可选）
code --install-extension ctagsx
```

> Remote-WSL 模式下扩展需安装在 WSL 侧，扩展面板中点击 "Install in WSL: Ubuntu"。

### 1.3 排除 out/ 目录（关键，避免卡顿）

> 为什么：AOSP `out/` 有数十 GB 构建产物，索引会导致 VS Code 严重卡顿。

在 AOSP 源码根目录创建 `.vscode/settings.json`：

```bash
mkdir -p /home/lechao/workspace/aosp/.vscode
cat > /home/lechao/workspace/aosp/.vscode/settings.json << 'EOF'
{
  "files.exclude": { "out": true },
  "files.watcherExclude": { "out/**": true },
  "search.exclude": { "out/**": true }
}
EOF
```

### 1.4 为 Kernel 配置 C/C++ 智能提示

在 Kernel 源码目录创建 `.vscode/c_cpp_properties.json`：

```bash
mkdir -p /home/lechao/workspace/rpi5-kernel-build/common/.vscode
cat > /home/lechao/workspace/rpi5-kernel-build/common/.vscode/c_cpp_properties.json << 'EOF'
{
  "configurations": [
    {
      "name": "Linux ARM64",
      "includePath": [
        "${workspaceFolder}/**",
        "${workspaceFolder}/include",
        "${workspaceFolder}/arch/arm64/include"
      ],
      "defines": ["__KERNEL__", "CONFIG_ARM64", "__aarch64__"],
      "compilerPath": "/home/lechao/workspace/rpi5-kernel-build/prebuilts/clang/host/linux-x86/clang-r522817/bin/clang",
      "cStandard": "c11",
      "intelliSenseMode": "linux-clang-arm64"
    }
  ],
  "version": 4
}
EOF
```

可选：为 Kernel 建立 cscope 索引加速符号跳转：

```bash
cd /home/lechao/workspace/rpi5-kernel-build/common
make ARCH=arm64 cscope
```

### 1.5 常见问题

- **左下角未显示 WSL 标识**：确认已装 Remote-WSL；在 WSL 终端用 `code .` 启动，不要从 Windows 开始菜单打开。
- **WSL 中 `code` 未找到**：WSL 执行 `code .` 自动添加，或手动加入 PATH。
- **打开 AOSP 卡顿**：排除 out/ 目录（1.3）。
- **C/C++ 提示不准确**：确认 `compilerPath` 指向正确 clang，Kernel 需 `__KERNEL__`/`CONFIG_ARM64` 宏。

---

## 2. OpenGrok 源码搜索引擎

> 为什么用 OpenGrok：VS Code 全局搜索对 AOSP（200GB+、80万+文件）力不从心。OpenGrok 基于 Apache Lucene 实现高速全文检索、定义跳转、交叉引用，是阅读 AOSP 源码的利器。

> **版本兼容性（硬性）**: OpenGrok 1.13+ 需要 Java 21；本环境 Java 17 → 使用 **OpenGrok 1.12.7**。版本不匹配报 `UnsupportedClassVersionError`。

### 2.1 安装依赖与目录结构

```bash
sudo apt update
sudo apt install -y universal-ctags tomcat10 tomcat10-admin
ctags --version

mkdir -p /home/lechao/opengrok/{data,dist,etc,log}
# dist=程序文件; data=索引数据(约源码30~50%,即60~100GB); etc=配置; log=日志
```

### 2.2 下载 OpenGrok 并部署

```bash
cd /tmp
curl -L -O https://github.com/oracle/opengrok/releases/download/1.12.7/opengrok-1.12.7.tar.gz
tar -C /home/lechao/opengrok/dist --strip-components=1 -xzf opengrok-1.12.7.tar.gz

# Tomcat 内存（AOSP 索引需要较大 JVM 堆）
sudo tee /usr/share/tomcat10/bin/setenv.sh > /dev/null << 'EOF'
JAVA_OPTS="$JAVA_OPTS -server -Xmx8g"
EOF
sudo chmod +x /usr/share/tomcat10/bin/setenv.sh

# 部署 Web 应用
sudo cp /home/lechao/opengrok/dist/lib/source.war /var/lib/tomcat10/webapps/
sudo systemctl restart tomcat10
```

### 2.3 配置日志与索引脚本

```bash
cp /home/lechao/opengrok/dist/doc/logging.properties /home/lechao/opengrok/etc/
# 编辑 logging.properties，将 FileHandler.pattern 改为:
# java.util.logging.FileHandler.pattern = /home/lechao/opengrok/log/opengrok%g.%u.log

cat > /home/lechao/opengrok/reindex.sh << 'EOF'
#!/bin/bash
ulimit -n 65536
java -Xmx16g -server \
  -Djava.util.logging.config.file=/home/lechao/opengrok/etc/logging.properties \
  -jar /home/lechao/opengrok/dist/lib/opengrok.jar \
  -c /usr/bin/ctags \
  -s /home/lechao/workspace/aosp \
  -d /home/lechao/opengrok/data \
  -H -P -S -G -T 6 -m 256 \
  -i d:out -i d:.repo \
  -W /home/lechao/opengrok/etc/configuration.xml \
  -U http://localhost:8080/source
EOF
chmod +x /home/lechao/opengrok/reindex.sh
```

关键参数：`-Xmx16g`（AOSP 推荐 16GB）、`-T 6`（并行度=CPU 核数一半）、`-i d:out -i d:.repo`（排除编译产物与元数据）。

### 2.4 执行索引与访问

```bash
/home/lechao/opengrok/reindex.sh
```

- 首次索引预计 **4~8 小时**（12核/32GB），索引数据 60~100GB；后续增量约 10~30 分钟。
- Windows 浏览器访问：`http://localhost:8080/source`（WSL2 默认 localhost 端口转发）。
- 源码更新后重新执行 reindex.sh 脚本（historyBasedReindex 只处理变更文件）。

### 2.5 源码目录权限问题（常见坑）

> 为什么：Tomcat 以 `tomcat` 用户运行，AOSP 源码目录属于当前用户，默认权限 750 不允许 tomcat 读取，报错 `Source root path does not exist`。

**方案1（推荐）**：开放读权限

```bash
chmod o+x /home/lechao
chmod o+x /home/lechao/workspace
chmod -R o+rX /home/lechao/workspace/aosp
```

**方案2**：tomcat 加入用户组

```bash
sudo usermod -aG lechao tomcat
sudo systemctl restart tomcat10
```

### 2.6 OpenGrok 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `UnsupportedClassVersionError` | OpenGrok 与 Java 版本不匹配 | Java 17→1.12.x；Java 21→1.13+ |
| 首次索引 OOM | JVM 堆不足 | 增大 `-Xmx` 至 16g+ |
| Windows 无法访问 localhost:8080 | 端口转发未生效 | `netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=$(wsl hostname -I)` |
| 索引速度极慢 | 未排除 out/.repo | 确认含 `-i d:out -i d:.repo` |
| `Source root path does not exist` | tomcat 用户无源码读权限 | 见 2.5 两种方案 |

---

## 3. 环境差异说明

本文档中的路径（`/home/lechao/workspace/aosp`、`/home/lechao/opengrok`、`clang-r522817`）为实际环境示例。若你的环境不同，替换为对应实际路径即可（对应关系参考 `harness/config/paths.conf` 的 `AOSP_WS`/`KERNEL_WS`）。

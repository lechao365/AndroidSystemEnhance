# RPI5 开发调试环境参考

> **规则 ID**: `DBG-001` ~ `DBG-012`
> **适用范围**: 涉及日志抓取、串口调试、WSL 下映射 USB 设备、调试命令固化时，AI 必须参考本文档。
> **参考来源**: 由开发调试环境教程重构而来（VS Code/OpenGrok 等人类 IDE 配置迁移至 `docs/development-tools.md`）。
>
> 前置：设备可启动并建立网络 ADB（见 `flash-deploy-reference.md`）。

---

## 1. 日志抓取与过滤

| 场景 | 命令 |
|------|------|
| 应用层/框架层问题 | `adb logcat` |
| 内核/驱动层问题 | `adb shell dmesg` |
| 设备无法启动/ADB 不可用 | 串口（见第 3 节） |

```bash
adb logcat | tee logcat.txt          # 系统日志存文件
adb shell dmesg | tee dmesg.txt      # 内核日志存文件
adb logcat -s <tag_name>             # 按标签过滤
adb logcat *:W                       # 按优先级（W 及以上）
adb logcat | grep <keyword>          # 按关键字过滤
```

> **DBG-001**: logcat 输出过多时用 `-s` 或 `grep` 过滤，不要在大量日志中盲目搜索。

---

## 2. 串口调试入口

> 为什么：串口是树莓派5 最底层调试入口，ADB 不可用或设备无法启动时尤为重要。树莓派5 **无 USB OTG**，串口 + 网络 ADB 是仅有的两个调试入口。

### 2.1 硬件接线

| 硬件 | 要求 |
|------|------|
| USB 转 TTL 串口模块 | CH340/CH341、CP2102、FT232 等，**必须支持 3.3V 电平** |
| 杜邦线 | 3 根（母对母）：TX、RX、GND |

接线（**TX/RX 交叉**）：

```
USB转TTL模块          树莓派5 GPIO
  TXD  ───────────→  RXD  (Pin 10, GPIO15)
  RXD  ←───────────  TXD  (Pin 8,  GPIO14)
  GND  ───────────→  GND  (Pin 6)
```

> **DBG-002**: 模块必须设置 3.3V 电平（CH340 跳线帽拨到 3.3V）；**不要接 VCC**（树莓派独立供电，只接 TX/RX/GND）。
> **DBG-003**: TX/RX **必须交叉连接**（模块 TX → 树莓派 RX，模块 RX → 树莓派 TX），直连无法通信。

### 2.2 树莓派5 串口编号（与树莓派4 的关键差异）

| 串口设备 | 硬件位置 | 别名 | 用途 |
|---------|---------|------|------|
| RP1 UART0 | 40-pin GPIO14/15（Pin 8/10） | `ttyAMA0`、`serial0` | **调试串口，接 USB 转串口模块** |
| SoC PL011 | SoC 内部，未引出到 40-pin | `ttyAMA10`、`serial10` | 系统内置 UART，不可外部调试 |
| RP1 UART1~4 | 40-pin 其他 GPIO | `ttyAMA1`~`ttyAMA4` | 扩展串口 |

> **DBG-004**: 树莓派5 调试串口必须用 **`ttyAMA0`**（RP1 UART0，40-pin 排针），**不要用 `ttyAMA10`**（SoC 内置，接不到排针）。树莓派4 的 `ttyAMA0` 就是 40-pin 串口，没有 ttyAMA10——不要照搬树莓派4 经验，否则串口收不到任何输出。

### 2.3 启用串口输出（源码修改 + 重编刷写）

> **DBG-005**: `config.txt` 和 `cmdline.txt` 都打包在 `boot.img` 中，修改源码后**必须重新编译 `bootimage` 并刷写**，不能靠改 SD 卡文件生效。

修改 `device/brcm/rpi5/boot/config.txt` 添加：

```txt
enable_uart=1
dtoverlay=uart0-pi5
```

修改 `device/brcm/rpi5/BoardConfig.mk`，`BOARD_KERNEL_CMDLINE` 改为：

```make
BOARD_KERNEL_CMDLINE := console=ttyAMA0,115200 no_console_suspend root=/dev/ram0 rootwait androidboot.hardware=rpi5
```

> 为什么改两处：`dtoverlay=uart0-pi5` 在硬件层启用 RP1 UART0 并绑定 GPIO14/15；`console=ttyAMA0` 告诉内核把启动日志输出到 RP1 UART0（40-pin）。**只改一处都无法在串口看到日志**。
>
> 此改动已归档：`code/rpi5/aosp/modified/device/brcm/rpi5/BoardConfig.mk.diff`，源码改动须从 `code/`（dev 分支）源头开始，`~/workspace/` 为编译缓存镜像（code → workspace 单向同步，SRC-001）。

重新编译刷写：

```bash
AOSP_ROOT="${AOSP_ROOT:-$HOME/workspace/aosp}"
cd "$AOSP_ROOT"
source build/envsetup.sh
lunch aosp_rpi5-bp1a-userdebug
make bootimage systemimage vendorimage -j$(nproc)
./rpi5-mkimg.sh     # 刷写见 flash-deploy-reference.md
```

### 2.4 主机端串口连接

**Linux/WSL 环境：**

```bash
sudo apt install -y minicom
sudo usermod -aG dialout $USER    # 加组，重新登录生效
sudo minicom -D /dev/ttyUSB0 -b 115200
# 或 picocom
sudo picocom -b 115200 /dev/ttyUSB0
```

> **DBG-006**: minicom **必须关闭硬件流控**（Ctrl+A → O → F 设为 No），否则收不到数据。

**Windows 原生环境**：PuTTY/MobaXterm，Serial 连接，端口 COM3（设备管理器查看），波特率 115200，8N1，无流控。

### 2.5 串口连接后的操作

```bash
# 连接 WiFi（wpa_cli 不可用，Android 走 Framework 层）
svc wifi enable
cmd wifi connect-network "WiFi名称" wpa2 "WiFi密码"
cmd wifi status
ip addr show wlan0

# 开启网络 ADB
setprop service.adb.tcp.port 5555 && stop adbd && start adbd
getprop init.svc.adbd
# 主机端: adb connect <设备IP>:5555

# 保存串口日志
sudo minicom -D /dev/ttyUSB0 -b 115200 -C serial_log.txt
```

> **DBG-007**: `wpa_cli` 在 Android 不可用，WiFi 连接用 `cmd wifi connect-network`。串口适合首次调试与排查，日常开发推荐网络 ADB（更快更便捷）。

---

## 3. WSL 下映射 USB 串口设备

> 为什么：WSL2 默认无法访问 Windows 上的 USB 设备，需 `usbipd-win` 映射。

**步骤 1**：Windows PowerShell（管理员）安装并绑定：

```powershell
winget install usbipd
usbipd list          # 找到 USB 转串口模块的 BUSID，如 1-2
usbipd bind --busid 1-2
usbipd attach --wsl --busid 1-2
```

**步骤 2**：WSL 中确认设备并加载驱动：

```bash
lsusb
ls /dev/ttyUSB*
sudo modprobe ftdi_sio    # FT232
sudo modprobe cp210x      # CP2102
sudo modprobe ch341       # CH340/CH341
sudo usermod -aG dialout $USER
```

> **DBG-008**: WSL 重启后 USB 映射丢失，需在 Windows 重新执行 `usbipd attach --wsl --busid X`。

---

## 4. 固化高频调试命令（可选）

写入 `~/.bashrc`：

```bash
alias ad='adb devices'
alias ar='adb root && adb remount'
alias al='adb logcat | tee logcat.txt'
alias ak='adb shell dmesg | tee dmesg.txt'
alias ash='adb shell'
alias bs='source build/envsetup.sh'
source ~/.bashrc
```

---

## 5. 约束总览

| ID | 约束 | 违反后果 |
|----|------|---------|
| DBG-001 | logcat 过多时用 -s/grep 过滤 | 无法定位问题 |
| DBG-002 | 串口模块必须 3.3V，不接 VCC | 电平不匹配乱码/损坏 |
| DBG-003 | TX/RX 必须交叉连接 | 串口无数据 |
| DBG-004 | 树莓派5 用 `ttyAMA0`，禁止 ttyAMA10 | 串口收不到输出 |
| DBG-005 | 串口配置改后必须重编 boot.img 刷写 | 改 SD 卡文件不生效 |
| DBG-006 | minicom 必须关闭硬件流控 | 收不到数据 |
| DBG-007 | WiFi 用 `cmd wifi`，禁止 wpa_cli | 无法连接 WiFi |
| DBG-008 | WSL 重启后需重新 usbipd attach | 串口不可用 |

---

## 6. 常见问题与排查

- **串口无输出**：查 TX/RX 交叉、`enable_uart=1`、`console=ttyAMA0,115200`、已重编刷写、minicom 关流控。
- **串口乱码**：波特率 115200；模块电平 3.3V。
- **WSL 无 ttyUSB 设备**：重新 `usbipd attach`，`modprobe` 对应驱动。
- **`usbipd` 命令未找到**：`winget install usbipd`。
- **adb/logcat/dmesg/串口各自适用场景**：adb=连接与文件操作；logcat=应用/框架层；dmesg=内核/驱动层；串口=设备无法启动或 ADB 不可用时的底层问题。

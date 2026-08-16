# RPI5 刷机与部署参考

> **规则 ID**: `FLASH-001` ~ `FLASH-007`
> **适用范围**: 涉及将 AOSP 构建产物写入 SD 卡、树莓派5 首次上电、建立 ADB/串口调试入口时，AI 必须参考本文档。
> **参考来源**: 由刷机与部署教程 + 树莓派5 硬件认知重构而来。
>
> 前置：构建产物准备见 `build-reference.md`。

---

## 1. 硬件认知（接线与接口事实）

> 为什么：接线错误是首上电最常见的失败原因，先明确硬件事实再操作。

| 项 | 事实 |
|----|------|
| SoC | BCM2712，4×Cortex-A76 @ 2.4GHz，VideoCore VII GPU |
| 内存 | 4GB 或 8GB LPDDR4X |
| 启动介质 | SD 卡槽是主启动介质；PCIe NVMe 需额外配置方可启动 |
| 显示 | 2×Micro HDMI，**靠近电源口的为 HDMI0（主显示）** |
| 网络 | 千兆以太网（靠近 USB 侧）+ 板载 WiFi 5 + BT 5.0 |
| 存储扩展 | 2×USB 3.0 + 2×USB 2.0；PCIe（经 HAT）可接 NVMe |
| 调试 | **无 USB OTG**，USB ADB 不可用，须用网络 ADB 或串口 |

**连接最小环境顺序**（**FLASH-001**）：SD 卡 → Micro HDMI(HDMI0) → 键鼠 → 网线/WiFi → **最后接电源**。

> **FLASH-001**: 电源必须最后连接，避免带电插拔其他接口。首次上电观察红色（电源）与绿色（活动）指示灯。

---

## 2. 确认构建产物

> 为什么：刷机优先使用完整镜像，若只有分区镜像则无法单独组成可启动 SD 卡。

```bash
AOSP_ROOT="${AOSP_ROOT:-$HOME/workspace/aosp}"
cd "$AOSP_ROOT"
ls out/target/product/rpi5/
```

| 产物 | 用途 |
|------|------|
| `RaspberryVanillaAOSP15-<日期>-rpi5.img` | **完整刷机镜像**（由 `rpi5-mkimg.sh` 生成，刷机优先使用） |
| `boot.img` | 启动分区镜像（含 kernel/ramdisk/config.txt） |
| `system.img` / `vendor.img` / `ramdisk.img` | 分区镜像 |

> **FLASH-002**: 若未执行过 `rpi5-mkimg.sh`，产物目录只有分区镜像、没有完整镜像，需先回到 `build-reference.md` 完成打包。

---

## 3. 写入镜像到 SD 卡（WSL 用户必读）

### 3.1 WSL 复制镜像到 Windows

> **FLASH-003**: WSL2 的 `wsl --mount` **不支持 USB 读卡器/SD 卡/Flash 盘**（微软已知限制，报错 `0x8007000f`）。必须使用 Windows 刷卡工具写入。

```bash
mkdir -p /mnt/c/Files/RaspberryImages/
cp out/target/product/rpi5/RaspberryVanillaAOSP15-*-rpi5.img /mnt/c/Files/RaspberryImages/
ls -lh /mnt/c/Files/RaspberryImages/RaspberryVanillaAOSP15-*-rpi5.img
```

> `/mnt/c/` 对应 Windows `C:\`，上述路径对应 `C:\Files\RaspberryImages\`。

### 3.2 Windows 刷卡工具写入

**步骤 1**：在 Windows PowerShell（管理员）核对磁盘号，**避免覆盖系统盘**：

```powershell
Get-Disk | Select-Object Number, FriendlyName, Size
```

> **FLASH-004**: 必须按容量/名称准确识别 TF 卡磁盘号，**选错会覆盖 Windows 系统盘**。

**步骤 2**：使用工具写入：
- **Raspberry Pi Imager**（官方，推荐）：Choose OS → Use custom → 选 `.img` → Choose Storage → 选 TF 卡 → Write
- **balenaEtcher**：Flash from file → 选 `.img` → Select target → 选 TF 卡 → Flash!

**步骤 3**：写入完成后安全移除 TF 卡（Windows 右下角"安全删除硬件"）。

---

## 4. 首次上电启动与观察

插入 SD 卡 → 连接显示器/键鼠 → **最后接电源**。

启动过程观察：
1. 红色电源指示灯常亮 → 供电正常
2. 绿色活动指示灯闪烁 → 正在读取 SD 卡
3. 显示器出现启动画面 → 显示输出正常
4. 出现 Android 桌面/Launcher → 系统启动完成

---

## 5. 建立调试入口（网络 ADB / 串口）

> 为什么：树莓派5 **无 USB OTG**，USB ADB 不可用，调试只能走网络 ADB 或串口。串口接线详见 `debug-tools-reference.md`。

首次启动后若 ADB 无法连接，最小流程（通过串口进入 shell）：

```bash
# 连接 WiFi（Android 走 Framework 层，wpa_cli 不可用）
cmd wifi connect-network "WiFi名称" wpa2 "WiFi密码"

# 开启网络 ADB
setprop service.adb.tcp.port 5555 && stop adbd && start adbd
```

主机端连接验证：

```bash
adb connect <设备IP>:5555
adb devices
adb shell
adb root          # userdebug 版本可获取 root
adb remount
```

> **FLASH-005**: 串口无输出时，确认源码 `config.txt` 有 `enable_uart=1` + `dtoverlay=uart0-pi5`，`BoardConfig.mk` 有 `console=ttyAMA0,115200`，且 TX/RX 交叉连接。详见 `debug-tools-reference.md` 串口章节。
>
> **FLASH-006**: `wpa_cli` 在 Android 中不可用，WiFi 连接必须用 `cmd wifi connect-network`。

---

## 6. 约束总览

| ID | 约束 | 违反后果 |
|----|------|---------|
| FLASH-001 | 电源必须最后连接 | 带电插拔损坏接口/设备 |
| FLASH-002 | 刷机必须用完整镜像 `RaspberryVanillaAOSP15-*-rpi5.img` | 分区镜像无法组成可启动卡 |
| FLASH-003 | WSL 必须用 Windows 刷卡工具，禁止 `wsl --mount` | 报错 0x8007000f，无法写入 |
| FLASH-004 | 选磁盘前必须 `Get-Disk` 核对容量/名称 | 覆盖 Windows 系统盘 |
| FLASH-005 | 串口无输出检查 ttyAMA0 + TX/RX 交叉 | 串口调试不可用 |
| FLASH-006 | WiFi 连接用 `cmd wifi connect-network`，禁止 wpa_cli | 无法连接 WiFi |
| FLASH-007 | HDMI 必须接 HDMI0（靠近电源口） | 上电无显示 |

---

## 7. 常见问题与排查

- **`wsl --mount` 报错 `0x8007000f`**：WSL2 不支持 USB 读卡器，改用 Windows 刷卡工具。
- **上电无任何显示**：检查 HDMI 是否接 HDMI0 口，确认 SD 卡镜像写入正确。
- **绿灯不闪烁**：SD 卡未被读取，检查插入到位、镜像是否可启动。
- **启动卡 Logo/黑屏**：通过串口查看启动日志，定位 kernel panic / 驱动加载 / init 卡住阶段。
- **ADB 无法连接**：树莓派5 无 USB OTG，走网络 ADB（串口连 WiFi → 开启 adb tcp → `adb connect`）。
- **串口无输出**：确认 `enable_uart=1`、`dtoverlay=uart0-pi5`、`console=ttyAMA0,115200`、TX/RX 交叉、已重新编译刷写。
- **SD 卡写入后无法启动**：确认镜像 sha256 完整、写入校验通过、SD 卡无硬件故障。
- **WiFi/蓝牙不可用**：raspberry-vanilla 已知 SELinux permissive、不支持 userdata 加密，部分功能需额外配置。

# Raspberry Pi 5 系统增强 Patches

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位
- **是什么**：Raspberry Pi 5 平台 AOSP + Linux kernel 定制改动的归档镜像（`~/workspace/` 编译源码树的精确镜像）
- **职责边界**：归档层，非编译树（编译在 `~/workspace/`）
- **上下游依赖**：由 `sync-workspace-to-code` 从 workspace 写入，被 `sync-code-to-workspace` 读回 workspace、被 `sync-code-to-doc` 读为文档源

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 顶层目录清单与职责 | 了解结构时 |
| [特性概览](#特性概览) | 内核态 / 用户态特性索引 | 了解改动范围时 |
| [使用方式](#使用方式) | 归档 / 同步 / 手动回写部署 | 实际操作时 🔖 |
| [文件映射表](#文件映射表) | 五张表（自动维护，请勿手编） | 查找具体文件时 🔖 |
| [关联资源](#关联资源) | workflow、规则、配置链接 | 深入理解时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口/被谁引用 |
|------------|------|------------------|
| `kernel/` | ← `~/workspace/rpi5-kernel-build/common/`，modified diff + new 全新文件 | 被 `sync-code-to-workspace` 读回 |
| `aosp/` | ← `~/workspace/aosp/`，modified diff + new 全新文件 | 被 `sync-code-to-workspace` 读回 |
| `others/` | 树莓派5专用工具，直接 Git 维护，不同步 | 独立编译运行 |
| `manifest.yaml` | 文件清单元数据，由 sync-workspace-to-code 维护 | 被 sync 工作流读取 |

### 特性概览

#### 内核态

| 特性 | 说明 |
|------|------|
| USB Storage notifier 基础设施 | 修改 transport.c/usb.c/usb.h，注入 TIMEOUT/STALL/DATA_CORRUPT/TRANSPORT_START/END 回调 |
| LcIod 内核驱动 | 新增 vendor/lechao/LcIod/（cdev 字符设备、ioctl 统计接口、event ring buffer） |
| LcView 内核打点框架 | 新增 vendor/lechao/LcView/（Builder 序列化 API、环形缓冲区、char 设备 `/dev/vendor_lechao_lcview`） |
| 内核编译系统 | Kbuild/Kconfig 添加 vendor/ 入口，defconfig 启用 USB_SERIAL_CONSOLE/DYNAMIC_DEBUG |

#### 用户态

| 特性 | 说明 |
|------|------|
| lechao_lciod HAL + Service | HAL（vendor 域）+ System Service（system 域）双进程分层架构 |
| lechao_lcview HAL + Daemon | HAL（epoll 批量读取）+ Daemon（Schema 校验 + JSONL 落盘） |
| Device tree 集成 | BoardConfig.mk、device.mk、sepolicy、ueventd 等 AOSP 设备配置 |

## 使用方式

本目录无可执行入口，作为归档承载层。

### 归档（workspace → code）

`/sync-workspace-to-code` 命令自动镜像 workspace 改动 + 更新 manifest + 更新本 README 文件映射表。

### 同步（code → workspace）

`/sync-code-to-workspace` 命令，详见 [`../../harness/skills/sync-code-to-workspace/SKILL.md`](../../harness/skills/sync-code-to-workspace/SKILL.md)。

### 手动回写部署（code → 新环境）

#### 内核

```bash
cd ~/workspace/rpi5-kernel-build/common

# 应用 modified patch
for f in $(find code/rpi5/kernel/modified -name '*.diff'); do
    patch -p1 < "$f"
done

# 复制新增模块
cp -r code/rpi5/kernel/new/vendor/* vendor/

# 配置与编译
make menuconfig   # 确认 CONFIG_VENDOR_LECHAO=y, CONFIG_LCVIEW=y
make -j$(nproc)
```

#### AOSP

```bash
cd ~/workspace/aosp

# 应用 modified patch
for f in $(find code/rpi5/aosp/modified -name '*.diff'); do
    patch -p1 < "$f"
done

# 复制新增文件
cp -r code/rpi5/aosp/new/device/* device/
cp -r code/rpi5/aosp/new/vendor/* vendor/

# 编译
source build/envsetup.sh
lunch aosp_rpi5-bp1a-userdebug
m vendorimage systemimage
```

#### 烧写与验证

```bash
adb reboot bootloader
fastboot flash vendor out/target/product/rpi5/vendor.img
fastboot flash system out/target/product/rpi5/system.img
fastboot reboot

# 验证进程
sleep 30
adb shell ps -A | grep lechao
# 期望：lechao_lciod_hal, lechao_lciod, lechao_lcview_hal, lechao_lcview

# 验证 VINTF
adb shell service list | grep lechao
# 期望：vendor.lechao.lciod.IIoHal, system.lechao.lciod.IIoService, vendor.lechao.lcview.ILcView

# 验证设备节点
adb shell ls -l /dev/vendor_lechao_lcview /dev/vendor_lechao_usbd*
```

## 文件映射表

> 以下映射表由 `sync-workspace-to-code` 自动维护，请勿手动编辑。

### kernel/modified/

| .diff 文件 | 目标源码路径 | 改动要点 |
|-----------|-------------|---------|
| `Kbuild.diff` | `rpi5-kernel-build/common/Kbuild` | 添加 `obj-y += vendor/` |
| `Kconfig.diff` | `rpi5-kernel-build/common/Kconfig` | 添加 `source "vendor/Kconfig"` |
| `arch/arm64/configs/android_rpi5_defconfig.diff` | `.../arch/arm64/configs/android_rpi5_defconfig` | 启用 USB_SERIAL_CONSOLE / FTDI / DYNAMIC_DEBUG |
| `drivers/usb/storage/transport.c.diff` | `.../drivers/usb/storage/transport.c` | notifier 回调（TIMEOUT/STALL/DATA_CORRUPT/TRANSPORT_START/END） |
| `drivers/usb/storage/usb.c.diff` | `.../drivers/usb/storage/usb.c` | notifier 基础设施 |
| `drivers/usb/storage/usb.h.diff` | `.../drivers/usb/storage/usb.h` | notifier 结构体和宏定义 |

### kernel/new/

| 路径 | 说明 |
|------|------|
| `vendor/Kconfig` / `vendor/Makefile` | 顶层 vendor 编译入口 |
| `vendor/lechao/Kconfig` / `Makefile` | lechao 子系统入口 |
| `vendor/lechao/kernel_lechao_log.h` | 统一日志宏定义 |
| `vendor/lechao/LcIod/` | LcIod 驱动（lciod_usbd.c/h、lciod_usbd-ioctl.h、lciod_usbd-stats.c、Kconfig、Makefile） |
| `vendor/lechao/LcView/` | LcView 打点框架（builder、ring、main、events.h、internal.h、ioctl.h、Kconfig、Makefile） |

> **注意**：`vendor/lechao/LcView/`、`vendor/lechao/Makefile` 在 git 中是 tracked 文件且当前有 diff（LcView/ 下 8 个文件 + Makefile）。按归档规则，vendor/lechao/ 整体视为新增目录，即使 git 显示 modified，也一律放 `new/`，不拆 diff。原来 tracked 的 `vendor/lechao/drivers/.../vendor_lechao_usbd-stats.c` 已在 working tree 中删除（逻辑重构至 LcIod），不归档。

### aosp/modified/

| .diff 文件 | 目标源码路径 | 改动要点 |
|-----------|-------------|---------|
| `device/brcm/rpi5/BoardConfig.mk.diff` | `device/brcm/rpi5/BoardConfig.mk` | UART ttyAMA0、`androidboot.selinux=permissive`（调试期 SELinux 宽容，自定义域 allow 规则补齐后可移除）、禁用 dm-verity、androidboot.verifiedbootstate=orange、SELINUX_IGNORE_NEVERALLOWS |
| `device/brcm/rpi5/README.md.diff` | `device/brcm/rpi5/README.md` | 新增串口配置文档 |
| `device/brcm/rpi5/aosp_rpi5.mk.diff` | `device/brcm/rpi5/aosp_rpi5.mk` | 跳过 VINTF kernel 校验 |
| `device/brcm/rpi5/boot/config.txt.diff` | `device/brcm/rpi5/boot/config.txt` | hdmi_force_hotplug=1、vc4-kms-v3d-pi5（RPi5 专用 overlay，修复无显示器时 surfaceflinger EGLConfig 崩溃）、UART overlay |
| `device/brcm/rpi5/device.mk.diff` | `device/brcm/rpi5/device.mk` | Soong 命名空间 + lciod/lcview 产品包 + WiFi 自动连接脚本 + `debug.sf.no_hwc=1`（无头模式 SF 软件合成，避免无 HDMI 时 HWC GraphicBufferMapper abort） |
| `device/brcm/rpi5/manifest.xml.diff` | `device/brcm/rpi5/manifest.xml` | 格式调整 |
| `device/brcm/rpi5/mkbootimg.mk.diff` | `device/brcm/rpi5/mkbootimg.mk` | 修复 boot 分区 Make 依赖缺失：config.txt/Image/dtb/overlays 加入 rpiboot 依赖，避免增量编译时 boot 改动不生效 |
| `device/brcm/rpi5/overlay/SettingsProviderRpiOverlay/res/values/defaults.xml.diff` | `.../SettingsProviderRpiOverlay/res/values/defaults.xml` | 默认设置覆盖 |
| `device/brcm/rpi5/ramdisk/init.rpi5.rc.diff` | `device/brcm/rpi5/ramdisk/init.rpi5.rc` | init 启动脚本调整 |
| `device/brcm/rpi5/ramdisk/ueventd.rpi5.rc.diff` | `.../ramdisk/ueventd.rpi5.rc` | lciod/lcview 设备节点权限 |
| `device/brcm/rpi5/sepolicy/file_contexts.diff` | `.../sepolicy/file_contexts` | lciod/lcview SELinux 文件上下文 |
| `device/brcm/rpi5/sepolicy/service_contexts.diff` | `.../sepolicy/service_contexts` | lciod/lcview 服务上下文 |
| `device/brcm/rpi5/vendor.prop.diff` | `device/brcm/rpi5/vendor.prop` | vendor 属性追加 |

### aosp/new/

| 路径 | 说明 |
|------|------|
| `device/brcm/rpi5/boot/wifi.conf` | WiFi 自动连接配置文件（SSID/PSK/key_mgmt） |
| `device/brcm/rpi5/ramdisk/init.rpi5.wifi.rc` | WiFi 连接服务 init.rc（seclabel 独立域 `u:r:rpi5_wifi_connect:s0`，boot_completed 触发 oneshot） |
| `device/brcm/rpi5/scripts/Android.bp` | rpi5-wifi-connect 脚本构建规则 |
| `device/brcm/rpi5/scripts/rpi5-wifi-connect.sh` | 开机自动连接 WiFi 脚本（读 wifi.conf → wpa_cli 连接 → 静态 IP 维持） |
| `device/brcm/rpi5/sepolicy/lechao_lciod.te` | lciod 系统服务 SELinux 策略 |
| `device/brcm/rpi5/sepolicy/lechao_lciod_hal.te` | lciod HAL SELinux 策略 |
| `device/brcm/rpi5/sepolicy/lechao_lcview.te` | lcview 系统服务 SELinux 策略 |
| `device/brcm/rpi5/sepolicy/lechao_lcview_hal.te` | lcview HAL SELinux 策略 |
| `device/brcm/rpi5/sepolicy/rpi5_wifi_connect.te` | WiFi 连接脚本独立 SELinux 域（init_daemon_domain + 完整 allow 规则：shell_exec/vfat/binder/netlink/capability/logd，支持 enforcing 模式） |
| `vendor/lechao/Android.bp` | Soong 命名空间声明 |
| `vendor/lechao/services/include/` | 共享头文件（lechao_log.h） |
| `vendor/lechao/services/lechao_lciod/` | IO 监控 HAL + System Service + common 公共工具库（minor 编号解析、ioctl 接口重构、HAL getStats 字段映射、Daemon getAverageRate 派生计算、HAL readEvent 排空策略） |
| `vendor/lechao/services/lechao_lcview/` | 打点框架 HAL + Daemon |

### others/

本目录为树莓派5专用工具/程序的独立存放区，直接通过 Git 提交维护，不涉及 workspace 同步。

| 路径 | 说明 |
|------|------|
| `usb-verify/` | USB 设备验证工具（Makefile + src/ + include/），含 ioctl 兼容层、CLI/check/device 子模块，ARM64 静态交叉编译（aarch64-linux-gnu-gcc -static），独立编译运行 |

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | [`../../harness/skills/sync-workspace-to-code/`](../../harness/skills/sync-workspace-to-code/) | 归档（workspace → code）（DEPRECATED） |
| 关联 workflow | [`../../harness/skills/sync-code-to-workspace/`](../../harness/skills/sync-code-to-workspace/) | 同步（code → workspace） |
| 关联 workflow | [`../../harness/skills/sync-code-to-doc/`](../../harness/skills/sync-code-to-doc/) | 文档同步 |
| 关联规则 | [`../../harness/rules/source-code-modify.md`](../../harness/rules/source-code-modify.md) | code/dev 是源头，workspace 是编译缓存（单向同步） |
| 关联配置 | [`../../harness/config/paths.conf`](../../harness/config/paths.conf) | 路径单一事实源（PATCHS_DIR 指向本目录） |

# Raspberry Pi 5 系统增强 Patches

## 概述

### 本目录用途

本目录归档 **Raspberry Pi 5** 平台的 AOSP 和 Linux kernel 定制改动。

### 工作流

```
workspace 改动 → 执行同步规则 → 更新本 README 文件映射表
```

每次在 `~/workspace/aosp` 或 `~/workspace/rpi5-kernel-build` 中完成改动后，按 [rules/sync_code_to_patchs.md](../../rules/sync_code_to_patchs.md) 执行归档，然后更新本文件的**文件映射表**（第 5 章）。

### 包含的特性

#### 内核态

| 特性 | 说明 |
|------|------|
| USB Storage notifier 基础设施 | 修改 transport.c/usb.c/usb.h，注入 TIMEOUT/STALL/DATA_CORRUPT/TRANSPORT_START/END 回调 |
| LcIod 内核驱动 | 新增 vendor/lechao/LcIod/（misc char 设备、ioctl 统计接口、event ring buffer） |
| LcView 内核打点框架 | 新增 vendor/lechao/LcView/（Builder 序列化 API、环形缓冲区、char 设备 `/dev/vendor_lechao_lcview`） |
| 内核编译系统 | Kbuild/Kconfig 添加 vendor/ 入口，defconfig 启用 USB_SERIAL_CONSOLE/DYNAMIC_DEBUG |

#### 用户态

| 特性 | 说明 |
|------|------|
| lechao_lciod HAL + Service | HAL（vendor 域）+ System Service（system 域）双进程分层架构 |
| lechao_lcview HAL + Daemon | HAL（epoll 批量读取）+ Daemon（Schema 校验 + JSONL 落盘） |
| Device tree 集成 | BoardConfig.mk、device.mk、sepolicy、ueventd 等 AOSP 设备配置 |

---

## 目录结构

```
rpi5/
├── README.md                      # 本文档
├── kernel/                        # ← ~/workspace/rpi5-kernel-build/common/
│   ├── modified/                  # 上游已有文件的 unified diff
│   │   ├── Kbuild.diff
│   │   ├── Kconfig.diff
│   │   ├── arch/arm64/configs/android_rpi5_defconfig.diff
│   │   └── drivers/usb/storage/
│   │       ├── transport.c.diff
│   │       ├── usb.c.diff
│   │       └── usb.h.diff
│   └── new/                       # 全部新增文件（完整文件）
│       └── vendor/
│           ├── Kconfig
│           ├── Makefile
│           └── lechao/
│               ├── Kconfig
│               ├── Makefile
│               ├── kernel_lechao_log.h
│               ├── LcIod/
│               └── LcView/
├── aosp/                          # ← ~/workspace/aosp/
│   ├── modified/                  # 上游已有文件的 unified diff
│   │   └── device/brcm/rpi5/
│   │       ├── BoardConfig.mk.diff
│   │       ├── README.md.diff
│   │       ├── aosp_rpi5.mk.diff
│   │       ├── boot/config.txt.diff
│   │       ├── device.mk.diff
│   │       ├── manifest.xml.diff
│   │       ├── ramdisk/ueventd.rpi5.rc.diff
│   │       ├── sepolicy/file_contexts.diff
│   │       └── sepolicy/service_contexts.diff
│   └── new/                       # 全部新增文件（完整文件）
│       ├── device/brcm/rpi5/sepolicy/
│       │   ├── lechao_lciod.te
│       │   ├── lechao_lciod_hal.te
│       │   ├── lechao_lcview.te
│       │   └── lechao_lcview_hal.te
│       └── vendor/lechao/
│           ├── Android.bp
│           └── services/
│               ├── include/
│               ├── lechao_lciod/
│               └── lechao_lcview/
└── others/                        # 树莓派5专用程序（直接提交到 Git，不同步）
```

---

## 回写命令（patchs → 新环境部署）

### 内核

```bash
cd ~/workspace/rpi5-kernel-build/common

# 应用 modified patch
for f in $(find patchs/rpi5/kernel/modified -name '*.diff'); do
    patch -p1 < "$f"
done

# 复制新增模块
cp -r patchs/rpi5/kernel/new/vendor/* vendor/

# 配置与编译
make menuconfig   # 确认 CONFIG_VENDOR_LECHAO=y, CONFIG_LCVIEW=y
make -j$(nproc)
```

### AOSP

```bash
cd ~/workspace/aosp

# 应用 modified patch
for f in $(find patchs/rpi5/aosp/modified -name '*.diff'); do
    patch -p1 < "$f"
done

# 复制新增文件
cp -r patchs/rpi5/aosp/new/device/* device/
cp -r patchs/rpi5/aosp/new/vendor/* vendor/

# 编译
source build/envsetup.sh
lunch aosp_rpi5-bp1a-userdebug
m vendorimage systemimage
```

### 烧写与验证

```bash
adb reboot bootloader
fastboot flash vendor out/target/product/rpi5/vendor.img
fastboot flash system out/target/product/rpi5/system.img
fastboot reboot

# 验证进程
sleep 30
adb shell ps -A | grep lechao
# 期望：lechao_lciod_hal, lechao_lciod, lechao.lcview-service, lechao_lcview

# 验证 VINTF
adb shell service list | grep lechao
# 期望：vendor.lechao.lciod.IIoHal, system.lechao.lciod.IIoService, vendor.lechao.lcview.ILcView

# 验证设备节点
adb shell ls -l /dev/vendor_lechao_lcview /dev/vendor_lechao_usbd*
```

---

## 文件映射表

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
| `device/brcm/rpi5/BoardConfig.mk.diff` | `device/brcm/rpi5/BoardConfig.mk` | UART ttyAMA0、SELinux 配置、禁用 dm-verity |
| `device/brcm/rpi5/README.md.diff` | `device/brcm/rpi5/README.md` | 新增串口配置文档 |
| `device/brcm/rpi5/aosp_rpi5.mk.diff` | `device/brcm/rpi5/aosp_rpi5.mk` | 跳过 VINTF kernel 校验 |
| `device/brcm/rpi5/boot/config.txt.diff` | `device/brcm/rpi5/boot/config.txt` | 启用 UART + uart0-pi5 overlay |
| `device/brcm/rpi5/device.mk.diff` | `device/brcm/rpi5/device.mk` | Soong 命名空间 + lciod/lcview 产品包 |
| `device/brcm/rpi5/manifest.xml.diff` | `device/brcm/rpi5/manifest.xml` | 格式调整 |
| `device/brcm/rpi5/ramdisk/ueventd.rpi5.rc.diff` | `.../ramdisk/ueventd.rpi5.rc` | lciod/lcview 设备节点权限 |
| `device/brcm/rpi5/sepolicy/file_contexts.diff` | `.../sepolicy/file_contexts` | lciod/lcview SELinux 文件上下文 |
| `device/brcm/rpi5/sepolicy/service_contexts.diff` | `.../sepolicy/service_contexts` | lciod/lcview 服务上下文 |

### aosp/new/

| 路径 | 说明 |
|------|------|
| `device/brcm/rpi5/sepolicy/lechao_lciod.te` | lciod 系统服务 SELinux 策略 |
| `device/brcm/rpi5/sepolicy/lechao_lciod_hal.te` | lciod HAL SELinux 策略 |
| `device/brcm/rpi5/sepolicy/lechao_lcview.te` | lcview 系统服务 SELinux 策略 |
| `device/brcm/rpi5/sepolicy/lechao_lcview_hal.te` | lcview HAL SELinux 策略 |
| `vendor/lechao/Android.bp` | Soong 命名空间声明 |
| `vendor/lechao/services/include/` | 共享头文件（lechao_log.h） |
| `vendor/lechao/services/lechao_lciod/` | IO 监控 HAL + System Service |
| `vendor/lechao/services/lechao_lcview/` | 打点框架 HAL + Daemon |

### others/

本目录为树莓派5专用工具/程序的独立存放区，直接通过 Git 提交维护，不涉及 workspace 同步。

| 路径 | 说明 |
|------|------|
| `usb-verify/` | USB 设备验证工具（Makefile + src/ + include/），含 ioctl 兼容层、CLI/check/device 子模块，独立编译运行 |

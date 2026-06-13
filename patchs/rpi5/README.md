# Raspberry Pi 5 系统增强 Patches

## 1. 概述

### 1.1 本目录用途

本目录归档 **Raspberry Pi 5** 平台的 AOSP 和 Linux kernel 定制改动。

### 1.2 工作流

```
workspace 改动 → 更新本 README.md → 参考 README.md 同步到 rpi5/
```

每次在 `~/workspace/aosp` 或 `~/workspace/rpi5-kernel-build` 中完成改动后：
1. 更新本文档的**文件映射表**（第 6 章）
2. 按本文档的**归档操作命令**（第 4 章）将改动同步到对应子目录
3. 执行**同步检查清单**（第 4.4 节）确认无遗漏

### 1.3 包含的特性

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

## 2. 归档规则

### 2.1 目录划分

| 目录 | 内容 | 数据来源 |
|------|------|---------|
| `kernel/modified/` | 对上游已有文件的改动（unified diff） | `git diff` → `.diff` 文件 |
| `kernel/new/` | 全部新增的文件/目录（完整文件） | 直接复制 |
| `aosp/modified/` | 对上游已有文件的改动（unified diff） | `git diff` 或 `repo diff` → `.diff` 文件 |
| `aosp/new/` | 全部新增的文件/目录（完整文件） | 直接复制 |
| `others/` | 树莓派5专用程序（测试工具等），独立维护，直接提交到 Git | 直接在 `others/` 目录中开发维护 |

### 2.2 modified vs new 判定标准

| 场景 | 归档方式 |
|------|---------|
| 修改了上游已有的文件（如 `transport.c`、`BoardConfig.mk`） | → `modified/`，生成 `.diff` |
| 新增文件/目录（如 `vendor/lechao/`、`lechao_*.te`） | → `new/`，复制完整文件 |
| 目录整体是新增的，其中的文件后续又有修改 | → `new/`，复制最新完整文件（不拆 diff） |
| vendor/lechao/ 下的内核模块和用户态服务 | → `new/`，整体视为新增 |

### 2.3 路径映射规则

**patchs 内路径 = 源码相对路径**

| patchs 路径 | 对应源码 |
|-------------|---------|
| `kernel/modified/drivers/usb/storage/transport.c.diff` | `rpi5-kernel-build/common/drivers/usb/storage/transport.c` |
| `kernel/new/vendor/lechao/LcIod/lciod_usbd.c` | `rpi5-kernel-build/common/vendor/lechao/LcIod/lciod_usbd.c` |
| `aosp/modified/device/brcm/rpi5/device.mk.diff` | `aosp/device/brcm/rpi5/device.mk` |
| `aosp/new/vendor/lechao/services/lechao_lciod/hal/hal_service.cpp` | `aosp/vendor/lechao/services/lechao_lciod/hal/hal_service.cpp` |
| `others/...` | 不对应特定源码树，独立归档 |

### 2.4 排除规则

以下内容**不归档**：

| 项目 | 原因 |
|------|------|
| `device/brcm/rpi5-kernel/` 下的二进制文件（Image、dtb、dtbo） | 编译产物 |
| `rpi5-kernel-build/prebuilts/` | 预编译工具链 |
| `rpi5-kernel-build/bazel-*` | 构建缓存 |
| 上游未改动的文件（git 无 diff） | 不属于本项目 |
| `vendor/lechao/LcIod/Module.symvers`、`*.o`、`*.ko`、`*.cmd` | 内核模块编译产物 |
| `others/` 目录 | 独立维护，直接提交 Git，不走归档流程 |

---

## 3. 目录结构

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

## 4. 归档操作命令

### 4.1 生成 modified/ 的 .diff 文件

```bash
# Kernel
cd ~/workspace/rpi5-kernel-build/common
git diff -- <源码相对路径> > /path/to/AndroidSystemEnhance/patchs/rpi5/kernel/modified/<同路径>.diff

# AOSP（repo 管理的项目用 git diff）
cd ~/workspace/aosp/device/brcm/rpi5
git diff -- <源码相对路径> > /path/to/AndroidSystemEnhance/patchs/rpi5/aosp/modified/device/brcm/rpi5/<同路径>.diff
```

### 4.2 复制 new/ 的完整文件

```bash
cp -r <源码路径> /path/to/AndroidSystemEnhance/patchs/rpi5/<kernel或aosp>/new/<相对路径>
```

### 4.3 others/ 目录

`others/` 不走 workspace → patchs 的同步流程。该目录下的工具/程序直接在 `others/` 中开发和维护，通过 Git 提交到本仓库。无需执行归档操作。

### 4.4 同步检查清单

归档完成后，执行以下检查确保无遗漏：

```bash
# 1. Kernel: modified（排除 vendor/lechao/ 整体，归入 new/）
cd ~/workspace/rpi5-kernel-build/common
PATCH_ROOT=/mnt/d/Code/Github/AndroidSystemEnhance/patchs/rpi5

echo "=== Kernel modified (M) ==="
git diff --name-only | grep -v '^vendor/lechao/' | while read f; do
    target="$PATCH_ROOT/kernel/modified/${f}.diff"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done

echo "=== Kernel new (untracked ??) ==="
git ls-files --others --exclude-standard | grep -v -E '\.(o|ko|cmd|symvers)$' | while read f; do
    target="$PATCH_ROOT/kernel/new/${f}"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done

echo "=== Kernel new (vendor/lechao/ 整体 — tracked 也归入 new/) ==="
find vendor/lechao -type f | grep -v -E '\.(o|ko|cmd|symvers)$|Module\.symvers' | while read f; do
    target="$PATCH_ROOT/kernel/new/${f}"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done

# 2. AOSP: repo 管理的项目，逐项目检查
cd ~/workspace/aosp
PATCH_ROOT=/mnt/d/Code/Github/AndroidSystemEnhance/patchs/rpi5

for proj_dir in $(repo status 2>/dev/null | grep "^project" | awk '{print $2}' | sed 's|/$||'); do
    [ "$proj_dir" = "device/brcm/rpi5-kernel" ] && continue  # 二进制，跳过

    cd ~/workspace/aosp/$proj_dir

    echo "=== AOSP $proj_dir modified (M) ==="
    git diff --name-only | while read f; do
        target="$PATCH_ROOT/aosp/modified/${proj_dir}/${f}.diff"
        [ -f "$target" ] && echo "  OK: ${proj_dir}/${f}" || echo "  MISS: ${proj_dir}/${f}"
    done

    echo "=== AOSP $proj_dir new (??) ==="
    git ls-files --others --exclude-standard | while read f; do
        target="$PATCH_ROOT/aosp/new/${proj_dir}/${f}"
        [ -f "$target" ] && echo "  OK: ${proj_dir}/${f}" || echo "  MISS: ${proj_dir}/${f}"
    done
done

# 3. AOSP: 非 repo 管理的目录（显式清单）
cd ~/workspace/aosp
echo "=== AOSP non-repo directories ==="
find vendor/lechao -type f | while read f; do
    target="$PATCH_ROOT/aosp/new/${f}"
    [ -f "$target" ] && echo "  OK: $f" || echo "  MISS: $f"
done
```

### 4.5 更新本文档

归档完成后，在**第 6 章**文件映射表中补充新增/变更的条目。

---

## 5. 回写命令（patchs → 新环境部署）

### 5.1 内核

```bash
cd ~/workspace/rpi5-kernel-build/common

# 1. 应用 modified patch
for f in $(find patchs/rpi5/kernel/modified -name '*.diff'); do
    patch -p1 < "$f"
done

# 2. 复制新增模块
cp -r patchs/rpi5/kernel/new/vendor/* vendor/

# 3. 配置与编译
make menuconfig   # 确认 CONFIG_VENDOR_LECHAO=y, CONFIG_LCVIEW=y
make -j$(nproc)
```

### 5.2 AOSP

```bash
cd ~/workspace/aosp

# 1. 应用 modified patch
for f in $(find patchs/rpi5/aosp/modified -name '*.diff'); do
    patch -p1 < "$f"
done

# 2. 复制新增文件
cp -r patchs/rpi5/aosp/new/device/* device/
cp -r patchs/rpi5/aosp/new/vendor/* vendor/

# 3. 编译
source build/envsetup.sh
lunch aosp_rpi5-bp1a-userdebug
m vendorimage systemimage
```

### 5.3 烧写与验证

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

## 6. 文件映射表

### 6.1 kernel/modified/

| .diff 文件 | 目标源码路径 | 改动要点 |
|-----------|-------------|---------|
| `Kbuild.diff` | `rpi5-kernel-build/common/Kbuild` | 添加 `obj-y += vendor/` |
| `Kconfig.diff` | `rpi5-kernel-build/common/Kconfig` | 添加 `source "vendor/Kconfig"` |
| `arch/arm64/configs/android_rpi5_defconfig.diff` | `.../arch/arm64/configs/android_rpi5_defconfig` | 启用 USB_SERIAL_CONSOLE / FTDI / DYNAMIC_DEBUG |
| `drivers/usb/storage/transport.c.diff` | `.../drivers/usb/storage/transport.c` | notifier 回调（TIMEOUT/STALL/DATA_CORRUPT/TRANSPORT_START/END） |
| `drivers/usb/storage/usb.c.diff` | `.../drivers/usb/storage/usb.c` | notifier 基础设施 |
| `drivers/usb/storage/usb.h.diff` | `.../drivers/usb/storage/usb.h` | notifier 结构体和宏定义 |

### 6.2 kernel/new/

| 路径 | 说明 |
|------|------|
| `vendor/Kconfig` / `vendor/Makefile` | 顶层 vendor 编译入口 |
| `vendor/lechao/Kconfig` / `Makefile` | lechao 子系统入口 |
| `vendor/lechao/kernel_lechao_log.h` | 统一日志宏定义 |
| `vendor/lechao/LcIod/` | LcIod 驱动（lciod_usbd.c/h、lciod_usbd-ioctl.h、lciod_usbd-stats.c、Kconfig、Makefile） |
| `vendor/lechao/LcView/` | LcView 打点框架（builder、ring、main、events.h、internal.h、ioctl.h、Kconfig、Makefile） |

> **注意**：`vendor/lechao/LcView/`、`vendor/lechao/Makefile` 在 git 中是 tracked 文件且当前有 diff（LcView/ 下 8 个文件 + Makefile）。按 §2.2 规则，vendor/lechao/ 整体视为新增目录，即使 git 显示 modified，也一律放 `new/`，不拆 diff。原来 tracked 的 `vendor/lechao/drivers/.../vendor_lechao_usbd-stats.c` 已在 working tree 中删除（逻辑重构至 LcIod），不归档。

### 6.3 aosp/modified/

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

### 6.4 aosp/new/

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

### 6.5 others/

本目录为树莓派5专用工具/程序的独立存放区，直接通过 Git 提交维护，不涉及 workspace 同步。

| 路径 | 说明 |
|------|------|
| `usb-verify/` | USB 设备验证工具（Makefile + src/ + include/），含 ioctl 兼容层、CLI/check/device 子模块，独立编译运行 |

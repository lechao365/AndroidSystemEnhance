# RPI5 增量开发与上板调试参考

> **规则 ID**: `INC-001` ~ `INC-010`
> **适用范围**: 涉及模块级修改、增量编译、镜像推送、内核替换、回退时，AI 必须参考本文档。
> **参考来源**: 由增量开发与上板调试教程重构而来。P1/P2 源码改动已归档于 `code/rpi5/aosp/modified/`，不再重复记录补丁全文。
>
> 前置：设备可启动并建立网络 ADB 连接（见 `flash-deploy-reference.md`）。

---

## 1. 核心原则

> 为什么：避免每次全量重刷。建立"改一点、编一点、替一点、验一点"的最小增量闭环，并能快速回退。

- **INC-001**: 不要执行 `make clean` 或 `make clobber`，保留 `out/` 目录（增量编译依赖）；彻底清理单个模块用 `m clean-<module_name>`。
- **INC-002**: 增量编译必须选对目标/镜像，避免不必要全量编译（见 `build-reference.md` BLD-008）。

---

## 2. 增量编译命令速查

| 修改内容 | 编译命令 | 耗时 |
|---------|---------|------|
| 单个模块 | `m <module_name>` / `mm` / `mmm <path>` | 最快 |
| kernel / ramdisk / init | `make bootimage` | 1~5 min |
| 框架层 / 系统应用 | `make systemimage` | 5~30 min |
| 厂商模块 / HAL | `make vendorimage` | 1~10 min |
| 不确定影响范围 | `make droid` | 自动增量 |

```bash
AOSP_ROOT="${AOSP_ROOT:-$HOME/workspace/aosp}"
cd "$AOSP_ROOT"
source build/envsetup.sh
lunch aosp_rpi5-bp1a-userdebug

m <module_name>
make bootimage systemimage vendorimage -j$(nproc)
```

> lunch 可选目标：`aosp_rpi5-bp1a-userdebug`（平板 UI，推荐）、`aosp_rpi5_tv-bp1a-userdebug`（TV）、`aosp_rpi5_car-bp1a-userdebug`（车机）。

---

## 3. 禁用 dm-verity（增量开发前置条件）

> 为什么：Android 默认 dm-verity 校验 system/vendor 分区，启用时 `adb remount` 报错 "Device must be bootloader unlocked"，无法推送修改文件。树莓派5 无传统 bootloader 锁，需从构建层面禁用。

**P1 禁用 dm-verity**（已在 workspace 应用并归档，见 `code/rpi5/aosp/modified/device/brcm/rpi5/BoardConfig.mk.diff`）：
- `BOARD_KERNEL_CMDLINE` 添加 `androidboot.verifiedbootstate=orange`
- 添加 `BOARD_BUILD_DISABLED_VBMETAIMAGE := true`

**P2 启用串口**（已归档，见 `BoardConfig.mk.diff` + `aosp_rpi5.mk.diff`）：
- `config.txt` 添加 `enable_uart=1` + `dtoverlay=uart0-pi5`
- `BoardConfig.mk` 将 `console=ttyAMA10` 改为 `console=ttyAMA0`

> 源码改动源头为 `code/`（dev 分支，SRC-001 纪律），经 workspace-verify 同步 workspace 编译验证。**禁止把 workspace 改动反向归档回 code**。

修改后重新编译并刷写：

```bash
cd "$AOSP_ROOT"
source build/envsetup.sh
lunch aosp_rpi5-bp1a-userdebug
make bootimage systemimage vendorimage -j$(nproc)
cd "$AOSP_ROOT" && ./rpi5-mkimg.sh
```

### 验证 dm-verity 已禁用

```bash
adb root
adb remount          # 输出 remount succeeded 即成功
adb shell getprop ro.boot.verifiedbootstate   # 预期 orange
adb shell cat /proc/cmdline | grep verifiedbootstate
```

> **INC-003**: `adb remount` 仍报 "Device must be bootloader unlocked" 时，优先检查 `ro.boot.verifiedbootstate` 是否为空（空 = `BOARD_KERNEL_CMDLINE` 未包含 orange 参数）。

---

## 4. 单独刷写 boot 分区（快速验证）

> 为什么：仅改 `config.txt`/`cmdline.txt`/内核时，无需重编 system/vendor，单独刷 boot 分区可大幅缩短验证周期。

**方式一：ADB 刷写（设备已启动）**

```bash
adb root
adb remount
adb push "$AOSP_ROOT/out/target/product/rpi5/boot.img" /sdcard/
adb shell dd if=/sdcard/boot.img of=/dev/block/mmcblk0p1 bs=4M   # 树莓派5 boot 分区 = mmcblk0p1
adb reboot
```

**方式二：SD 卡直接写入（设备未启动）**

```bash
lsblk    # 确认 SD 卡设备路径，如 /dev/sdX
sudo dd if="$AOSP_ROOT/out/target/product/rpi5/boot.img" of=/dev/sdX1 bs=4M status=progress conv=fsync
sync
sudo eject /dev/sdX
```

> **INC-004**: Windows 下用 Imager/Etcher 写 boot.img 只写第一分区，**避免覆盖整个 SD 卡**。推荐 Linux/WSL 或 ADB 方式。

---

## 5. 模块级修改、推送、重启、验证、回退闭环

### 5.1 推送产物到板端

```bash
adb devices
adb root
adb remount
adb push "$AOSP_ROOT/out/target/product/rpi5/<path>/<binary>" /system/<path>/<binary>
```

### 5.2 重启服务或系统

```bash
adb shell stop <service_name> && adb shell start <service_name>   # 可独立重启的服务
adb reboot                                                         # 核心组件或不确定时
```

### 5.3 用日志/现象验证

```bash
adb logcat                    # 系统日志
adb shell dmesg               # 内核日志
adb shell getprop <property>  # 验证属性
adb shell ls -la <remote_file>  # 验证文件替换
```

### 5.4 快速回退

```bash
# 方式一：恢复原文件
adb push <original_file> <remote_file>
adb reboot

# 方式二：git 恢复源码
git status && git diff
git checkout -- <modified_file>

# 方式三：重新刷入完整镜像（见 flash-deploy-reference.md）
```

> **INC-005**: `adb root` 失败时确认构建类型为 `eng`/`userdebug`，`user` 版本不支持 root。

---

## 6. 内核增量编译与更新

> 背景：树莓派5 在 AOSP 中使用**预编译内核**（`device/brcm/rpi5-kernel/Image`），源码树不含内核。要改内核需单独获取源码（raspberry-vanilla 预编译内核为 6.6.116，基于 `common-android15-6.6-lts` 基线）。完整编译命令见 `build-reference.md` 第 1 节，此处只记录流程要点。

**获取内核源码**（约 25GB）：

```bash
KERNEL_WS="${KERNEL_WS:-$HOME/workspace/rpi5-kernel-build}"
mkdir -p "$KERNEL_WS" && cd "$KERNEL_WS"
repo init -u https://android.googlesource.com/kernel/manifest -b common-android15-6.6-lts
curl -o .repo/local_manifests/manifest_brcm_rpi.xml -L \
  https://raw.githubusercontent.com/raspberry-vanilla/android_kernel_manifest/android-15.0/manifest_brcm_rpi.xml --create-dirs
repo sync -c -j4
```

**替换预编译内核**：

```bash
cd "$AOSP_ROOT/device/brcm/rpi5-kernel"
cp Image Image.prebuilt                              # 首次备份
cp bcm2712-rpi-5-b.dtb bcm2712-rpi-5-b.dtb.prebuilt
cp bcm2712d0-rpi-5-b.dtb bcm2712d0-rpi-5-b.dtb.prebuilt
cp -a overlays overlays.prebuilt

OUT="$KERNEL_WS/out/android_rpi5"
cp "$OUT/arch/arm64/boot/Image" Image
cp "$OUT"/arch/arm64/boot/dts/broadcom/bcm2712*.dtb .
cp "$OUT"/arch/arm64/boot/dts/overlays/*.dtbo overlays/
strings Image | grep "Linux version"
```

> **INC-006**: `Image`、`bcm2712-rpi-5-b.dtb`、`bcm2712d0-rpi-5-b.dtb`、`overlays/*.dtbo` **必须全部来自同一次编译，不能混用**，否则启动失败。

> **INC-007**: 自编译内核需绕过 VINTF 内核版本检查，在 `aosp_rpi5.mk` 末尾追加 `PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS := false`，然后重新 `make bootimage`。

**内核回退**：

```bash
cd "$AOSP_ROOT/device/brcm/rpi5-kernel"
git checkout -- Image bcm2712-rpi-5-b.dtb bcm2712d0-rpi-5-b.dtb overlays/
# 或从备份恢复：
# cp Image.prebuilt Image
# cp bcm2712-rpi-5-b.dtb.prebuilt bcm2712-rpi-5-b.dtb
# cp bcm2712d0-rpi-5-b.dtb.prebuilt bcm2712d0-rpi-5-b.dtb
# rm -rf overlays && mv overlays.prebuilt overlays
make bootimage -j$(nproc) && 重新刷写
```

---

## 7. 约束总览

| ID | 约束 | 违反后果 |
|----|------|---------|
| INC-001 | 禁止 `make clean`/`make clobber`，保留 out/ | 全量重编，浪费 1~4h |
| INC-002 | 增量编译必须选对目标/镜像 | 不必要全量编译 |
| INC-003 | remount 失败先查 `ro.boot.verifiedbootstate` 是否 orange | 误判 dm-verity 配置 |
| INC-004 | boot.img 单独刷写只写第一分区 | 覆盖整个 SD 卡 |
| INC-005 | `adb root` 需 eng/userdebug 构建 | user 版无法 root |
| INC-006 | 内核产物必须同一次编译，不可混用 | 启动失败 |
| INC-007 | 自编译内核必须加 VINTF 绕过 | VINTF 检查失败 |
| INC-008 | 源码改动必须先从 `code/`（dev 分支）出发，再同步到 workspace（`SRC-004`） | 改动源头丢失/workspace 与 code 不一致 |
| INC-009 | 内核编译必须用 `android_rpi5_defconfig`，禁止 `bcm2712_defconfig` | 启动失败/卡 Logo |
| INC-010 | 串口配置修改后必须重编 boot.img 刷写 | 改 SD 卡文件不生效（config.txt 打包在 boot.img） |

---

## 8. 常见问题与排查

- **adb root 失败**：确认构建类型 eng/userdebug；检查 adbd 启动、网络 ADB 稳定。
- **adb remount 失败**：先 `adb root`；仍报 unlocked 则查 `ro.boot.verifiedbootstate` 是否 orange。
- **推送后文件权限异常**：`adb shell chmod` / `chown` 修正。
- **修改后无法启动**：串口看日志，SD 卡重刷完整镜像回退。
- **模块编译报依赖错误**：确认 `source build/envsetup.sh` + `lunch` 已执行，检查模块名。
- **内核配置项不生效**：确认用 `android_rpi5_defconfig`；`make O=out ARCH=arm64 menuconfig` 检查。
- **替换内核后反复重启卡 Logo**：确认 dtb/overlays 已同步更新，回退预编译内核验证是否内核配置问题。
- **内核版本不匹配**：用 raspberry-vanilla 官方 `android_kernel_brcm_rpi` `android-15.0` 分支（同源 commit `06e9d6ca8fe9`），`strings Image | grep "Linux version"` 查看版本。
- **只替换 Image 未同步 dtb/overlays**：内核与设备树不匹配导致启动失败。

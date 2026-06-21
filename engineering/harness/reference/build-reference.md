# RPI5 编译参考

> **规则 ID**: `BLD-001` ~ `BLD-008`
> **适用范围**: 涉及 RPI5 AOSP/内核编译时，AI 必须优先参考本文档获取正确命令，**禁止自行猜测或使用错误参数**。
> **参考来源**: 本文档命令提取自 `engineering/harness/scripts/mk_rpi5_full_image.sh`，是该脚本编译逻辑的事实提取。
>
> 绝大多数编译场景读本文档即可，无需打开 `mk_rpi5_full_image.sh`。

---

## 1. 内核编译

> 对应 `mk_rpi5_full_image.sh` 第 160~283 行。

编译环境通过 `harness-paths.conf` 或环境变量指定（详见 `path-management.md`）。以下命令模板可直接复制执行。

### 1.1 设置环境变量

```bash
# 从 harness-paths.conf 读取的默认路径（可被对应环境变量覆盖）
export KERNEL_SRC="${KERNEL_SRC:-$HOME/workspace/rpi5-kernel-build/common}"
export KERNEL_OUT="${KERNEL_OUT:-$HOME/workspace/rpi5-kernel-build/out/android_rpi5}"
export CLANG_BIN="${CLANG_BIN:-$HOME/workspace/rpi5-kernel-build/prebuilts/clang/host/linux-x86/clang-r522817/bin}"
export KERNEL_DEST="${KERNEL_DEST:-$HOME/workspace/aosp/device/brcm/rpi5-kernel}"

export ARCH=arm64
export PATH="${CLANG_BIN}:${PATH}"

BUILD_JOBS=${BUILD_JOBS:-$(nproc)}
KERNEL_DEFCONFIG="android_rpi5_defconfig"
```

### 1.2 编译内核 Image + dtbs

> **BLD-001**: 必须使用 AOSP Clang + LLD 工具链，禁止使用 `CROSS_COMPILE=aarch64-linux-androidkernel-` 或系统 PATH 中的 GCC/clang。

```bash
cd "$KERNEL_SRC"

# 应用 defconfig
make O="${KERNEL_OUT}" \
    ARCH=arm64 \
    CC=clang \
    LD=ld.lld \
    AR=llvm-ar \
    NM=llvm-nm \
    STRIP=llvm-strip \
    OBJCOPY=llvm-objcopy \
    OBJDUMP=llvm-objdump \
    READELF=llvm-readelf \
    HOSTCC=clang \
    HOSTCXX=clang++ \
    HOSTAR=llvm-ar \
    HOSTLD=ld.lld \
    CROSS_COMPILE=aarch64-linux-gnu- \
    CLANG_TRIPLE=aarch64-linux-gnu- \
    ${KERNEL_DEFCONFIG}

# 编译内核 Image + 设备树 dtb + overlays
make O="${KERNEL_OUT}" \
    ARCH=arm64 \
    CC=clang \
    LD=ld.lld \
    AR=llvm-ar \
    NM=llvm-nm \
    STRIP=llvm-strip \
    OBJCOPY=llvm-objcopy \
    OBJDUMP=llvm-objdump \
    READELF=llvm-readelf \
    HOSTCC=clang \
    HOSTCXX=clang++ \
    HOSTAR=llvm-ar \
    HOSTLD=ld.lld \
    CROSS_COMPILE=aarch64-linux-gnu- \
    CLANG_TRIPLE=aarch64-linux-gnu- \
    Image dtbs -j${BUILD_JOBS}
```

> **BLD-002**: 内核编译目标必须是 `Image dtbs`（Image = 非压缩内核，dtbs = bcm2712*.dtb + overlays/*.dtbo）。禁止使用 `zImage` 或 `Image.gz`。

### 1.3 验证内核产物

```bash
KERNEL_IMAGE="${KERNEL_OUT}/arch/arm64/boot/Image"
# 检查 Image 存在
ls -lh "$KERNEL_IMAGE"
strings "$KERNEL_IMAGE" | grep "Linux version" | head -1

# 检查关键 dtb
ls -lh "${KERNEL_OUT}/arch/arm64/boot/dts/broadcom/bcm2712-rpi-5-b.dtb"
ls -lh "${KERNEL_OUT}/arch/arm64/boot/dts/broadcom/bcm2712d0-rpi-5-b.dtb"
```

### 1.4 同步内核产物到 AOSP

> **BLD-003**: 内核产物必须拷贝到 `$AOSP_ROOT/device/brcm/rpi5-kernel/`（`make bootimage` 从此目录取 Image）。

```bash
KERNEL_DTS_DIR="${KERNEL_OUT}/arch/arm64/boot/dts/broadcom"
KERNEL_OVERLAYS_DIR="${KERNEL_DTS_DIR}/overlays"

# 备份预编译内核（首次编译时）
cd "$KERNEL_DEST"
[ ! -f Image.prebuilt ] && {
    cp Image Image.prebuilt
    cp bcm2712-rpi-5-b.dtb bcm2712-rpi-5-b.dtb.prebuilt
    cp bcm2712d0-rpi-5-b.dtb bcm2712d0-rpi-5-b.dtb.prebuilt
    cp -a overlays overlays.prebuilt
}

# 拷贝新内核
cp "$KERNEL_IMAGE" "$KERNEL_DEST/Image"
cp "${KERNEL_DTS_DIR}"/bcm2712*.dtb "$KERNEL_DEST/"
rm -f "${KERNEL_DEST}/overlays/"*.dtbo 2>/dev/null || true
cp "${KERNEL_OVERLAYS_DIR}"/*.dtbo "${KERNEL_DEST}/overlays/"
```

---

## 2. AOSP 编译

> 对应 `mk_rpi5_full_image.sh` 第 289~390 行。

### 2.1 初始化编译环境

```bash
AOSP_ROOT="${AOSP_ROOT:-$HOME/workspace/aosp}"
LUNCH_TARGET="aosp_rpi5-bp1a-userdebug"

cd "$AOSP_ROOT"
source build/envsetup.sh
lunch "$LUNCH_TARGET"
```

> **BLD-004**: `lunch` 前必须先 `source build/envsetup.sh`。`lunch` 后 `ANDROID_PRODUCT_OUT` 必须非空，否则说明 lunch 失败。

### 2.2 编译 AOSP 镜像

> **BLD-005**: 禁止独立运行 `make` 不带目标。必须指定以下一个或多个目标，且 `bootimage` 必须在 `systemimage`/`vendorimage` 之前或同时编译。

```bash
BUILD_JOBS=${BUILD_JOBS:-$(nproc)}

# 编译 boot 镜像（含内核）
make bootimage -j${BUILD_JOBS}

# 编译 system 镜像
make systemimage -j${BUILD_JOBS}

# 编译 vendor 镜像
make vendorimage -j${BUILD_JOBS}

# 或一次编译多个
make bootimage systemimage vendorimage -j${BUILD_JOBS}
```

### 2.3 验证 AOSP 产物

```bash
ls -lh "${ANDROID_PRODUCT_OUT}/boot.img"
ls -lh "${ANDROID_PRODUCT_OUT}/system.img"
ls -lh "${ANDROID_PRODUCT_OUT}/vendor.img"
```

> **BLD-006**: 打包前必须确认三个镜像（`boot.img` / `system.img` / `vendor.img`）均已生成。缺少任一镜像都会导致 `rpi5-mkimg.sh` 执行失败。

---

## 3. 打包刷机镜像

> 对应 `mk_rpi5_full_image.sh` 第 396~438 行。

```bash
cd "$AOSP_ROOT"
VERSION_PREFIX="RaspberryVanillaAOSP15"

# 清理旧镜像（rpi5-mkimg.sh 遇到同名文件会报错）
rm -f "${ANDROID_PRODUCT_OUT}/${VERSION_PREFIX}"-*-rpi5.img

# 执行打包（需要 sudo 进行分区/格式化/losetup）
# 注意：sudo 清空环境变量，必须显式传递
sudo TARGET_PRODUCT="${TARGET_PRODUCT}" \
     ANDROID_PRODUCT_OUT="${ANDROID_PRODUCT_OUT}" \
     ./rpi5-mkimg.sh
```

> **BLD-007**: 打包必须通过 `sudo` 显式传递 `TARGET_PRODUCT` 和 `ANDROID_PRODUCT_OUT`，禁止用 `sudo -E` 或 `sudo` 不加环境变量。

### 产物路径

- 刷机包：`${ANDROID_PRODUCT_OUT}/${VERSION_PREFIX}-<date>-rpi5.img`
- 同步到 Windows（WSL）：`/mnt/c/Files/RaspberryImages/`

---

## 4. 一键脚本速查

`engineering/harness/scripts/mk_rpi5_full_image.sh` 封装了上述所有步骤，支持以下 mode：

| Mode | 行为 | 典型耗时 | 适用场景 |
|------|------|---------|---------|
| 0 | 仅打包已有镜像 | ~3min | 镜像已就绪，仅需重新打包分发 |
| 1 | 全量编译（内核 + boot + system + vendor + 打包） | 1~4h | 首次构建 / 全量重建 |
| 2 | 仅内核 + bootimage + 打包 | 10~25min | 经常改内核 |
| 3 | 仅 vendorimage + 打包 | 5~15min | 只改 vendor 分区/HAL 层 |
| 4 | 仅 systemimage + 打包 | 5~20min | 只改框架层/应用层 |

```bash
# 用法
./mk_rpi5_full_image.sh              # 默认 mode=1
./mk_rpi5_full_image.sh -mode 2      # 仅编译内核 + bootimage
./mk_rpi5_full_image.sh -mode 0      # 仅打包
BUILD_JOBS=4 ./mk_rpi5_full_image.sh # 自定义并行数
```

> **BLD-008**: 增量编译时**必须选择正确的 mode**（2/3/4），避免每次全量编译浪费时间。

---

## 5. 编译约束总览

| ID | 约束 | 违反后果 |
|----|------|---------|
| BLD-001 | 内核必须用 AOSP Clang + LLD 工具链（`CC=clang LD=ld.lld` 等全套），禁止系统 GCC/clang | 编译失败或生成错误内核 |
| BLD-002 | 内核目标 `Image dtbs`，禁止 `zImage`/`Image.gz` | 树莓派5 无法启动 |
| BLD-003 | 内核产物必须拷贝到 `device/brcm/rpi5-kernel/` | `bootimage` 使用旧内核 |
| BLD-004 | `lunch` 前必须先 `source build/envsetup.sh` | `lunch` 命令未找到 |
| BLD-005 | `make` 必须带目标（`bootimage`/`systemimage`/`vendorimage`），禁止裸 `make` | 全量编译耗时且可能出错 |
| BLD-006 | 打包前必须有 `boot.img` + `system.img` + `vendor.img` | `rpi5-mkimg.sh` 失败 |
| BLD-007 | `sudo` 打包必须显式传 `TARGET_PRODUCT`+`ANDROID_PRODUCT_OUT` | 环境变量丢失导致打包失败 |
| BLD-008 | 增量编译必须选对 mode，避免不必要全量 | 浪费 1~4h 编译时间 |

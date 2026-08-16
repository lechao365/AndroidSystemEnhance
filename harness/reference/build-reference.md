# RPI5 编译参考

> **规则 ID**: `BLD-001` ~ `BLD-012`
> **适用范围**: 涉及 RPI5 AOSP/内核编译时，AI 必须优先参考本文档获取正确命令，**禁止自行猜测或使用错误参数**。
> **参考来源**: 本文档命令提取自 `harness/scripts/mk_rpi5_full_image.sh`（编译逻辑）与 AOSP 构建环境准备教程（源码获取/ccache），是该脚本编译逻辑的事实提取。
>
> 绝大多数编译场景读本文档即可，无需打开 `mk_rpi5_full_image.sh`。
>
> 前置：宿主 WSL2 环境搭建见 `env-setup-reference.md`。

---

## 0. 构建环境准备（源码获取 / 依赖 / ccache）

### 0.1 获取 AOSP 源码

> **BLD-011**: 官方 AOSP 源码**不包含树莓派5设备配置**，无法编译出树莓派5可用镜像。必须添加 [raspberry-vanilla](https://github.com/raspberry-vanilla/android_local_manifest) 的 local manifest。

```bash
AOSP_ROOT="${AOSP_ROOT:-$HOME/workspace/aosp}"
mkdir -p "$AOSP_ROOT" && cd "$AOSP_ROOT"

# 初始化（国内镜像源优先；官方源需代理）
repo init -u https://mirrors.ustc.edu.cn/aosp/platform/manifest -b android-15.0.0_r32 --depth=1
# 或清华源: repo init -u https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/platform/manifest -b android-15.0.0_r32 --depth=1

# 添加树莓派5设备配置（需访问 GitHub，受限则配代理）
curl -o .repo/local_manifests/manifest_brcm_rpi.xml -L \
  https://raw.githubusercontent.com/raspberry-vanilla/android_local_manifest/android-15.0/manifest_brcm_rpi.xml --create-dirs
curl -o .repo/local_manifests/remove_projects.xml -L \
  https://raw.githubusercontent.com/raspberry-vanilla/android_local_manifest/android-15.0/remove_projects.xml

# 同步（--depth=1 浅克隆；镜像源限流 HTTP 429 时降 -j2/-j4 重试）
repo sync -j4 -c --no-tags --prune
```

> 提示：`repo sync` 中断后重新执行即可补齐；`repo sync -j1 --fail-fast` 可提高不稳定网络的完成率。源码约 100GB+，同步后应出现 `build/`、`frameworks/`、`device/brcm/rpi5/` 等目录。

### 0.2 安装编译依赖

```bash
sudo apt update
sudo apt install -y openjdk-17-jdk git gnupg flex bison gperf build-essential \
  zip curl zlib1g-dev gcc-multilib g++-multilib libc6-dev-i386 \
  libncurses-dev libx11-dev lib32z1-dev \
  libgl1-mesa-dev libxml2-utils xsltproc unzip
```

> **BLD-012**: 树莓派镜像打包还需额外依赖，缺少会直接导致 `rpi5-mkimg.sh` 失败：
> ```bash
> sudo apt install -y dosfstools e2fsprogs fdisk kpartx mtools rsync ccache
> ```

### 0.3 启用 ccache（首次编译前必须）

> **BLD-009**: `CCACHE_DIR` **必须**设为 AOSP `out/ccache`，不可指向其他路径（如 `~/.ccache`）。
> 原因：AOSP Soong 构建系统在 nsjail 沙箱内运行 Ninja 任务，沙箱将 `/` 只读挂载、仅 `out/` 可写。`CCACHE_DIR` 在 `out/` 外会触发 `ccache: error: Read-only file system` 导致编译失败。

```bash
export USE_CCACHE=1
export CCACHE_EXEC=$(which ccache)
export CCACHE_DIR="$AOSP_ROOT/out/ccache"
mkdir -p "$CCACHE_DIR"
ccache -M 50G    # 建议 50GB

# 持久化到 ~/.bashrc
echo 'export USE_CCACHE=1' >> ~/.bashrc
echo 'export CCACHE_EXEC=$(which ccache)' >> ~/.bashrc
echo "export CCACHE_DIR=$AOSP_ROOT/out/ccache" >> ~/.bashrc
```

常用命令：`ccache -s` 查看命中率，`ccache -C` 清空缓存。

### 0.4 编译并行数与交换空间

> 为什么：AOSP 编译内存密集，`-j` 过大会 OOM，swap 不足进程会被 SIGKILL。经验公式：并行数 ≈ CPU 核数，且不超过内存 GB 数的 1.5 倍（如 16GB/8核 → `-j12`）。

```bash
nproc
free -h
make bootimage systemimage vendorimage -j$(nproc)   # 默认全核；内存吃紧时降 -j

# 如需增加 swap（至少 16GB）
sudo fallocate -l 32G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

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

`harness/scripts/mk_rpi5_full_image.sh` 封装了上述所有步骤，支持以下 mode：

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
| BLD-009 | `CCACHE_DIR` 必须为 AOSP `out/ccache`，禁止其他路径 | ccache 报 Read-only file system，编译失败 |
| BLD-010 | 首次编译前必须启用 ccache（`USE_CCACHE=1`） | 首次/增量编译显著变慢 |
| BLD-011 | 源码必须含 raspberry-vanilla local manifest 设备配置 | 无法编译出树莓派5可用镜像 |
| BLD-012 | 必须安装 `dosfstools/e2fsprogs/fdisk/kpartx/mtools/rsync` 打包依赖 | `rpi5-mkimg.sh` 失败 |

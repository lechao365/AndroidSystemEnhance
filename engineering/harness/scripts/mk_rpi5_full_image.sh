#!/bin/bash
#==============================================================================
# 树莓派5 AOSP 一键编译打包脚本
#
# 功能：
#   根据 -mode 参数选择构建范围，最终通过 rpi5-mkimg.sh 生成可刷写 SD 卡的 .img
#
# 用法：
#   ./mk_rpi5_full_image.sh               # 默认 mode=1，全量编译
#   ./mk_rpi5_full_image.sh -mode 0       # 仅打包已有镜像
#   ./mk_rpi5_full_image.sh -mode 1       # 全量编译（内核 + boot + system + vendor + 打包）
#   ./mk_rpi5_full_image.sh -mode 2       # 仅编译内核 + bootimage + 打包
#   ./mk_rpi5_full_image.sh -mode 3       # 仅编译 vendor 镜像 + 打包
#   ./mk_rpi5_full_image.sh -mode 4       # 仅编译 system 镜像 + 打包
#   ./mk_rpi5_full_image.sh -h            # 显示帮助
#
# 环境变量：
#   BUILD_JOBS=N  自定义并行编译数（默认 nproc）
#
# 依赖：
#   内核源码:      /home/lechao/workspace/rpi5-kernel-build/common
#   内核工具链:    …/prebuilts/clang/…/clang-r522817/
#   AOSP 源码:     /home/lechao/workspace/aosp
#   预编译内核目录: ${AOSP_ROOT}/device/brcm/rpi5-kernel/
#   P1（dm-verity）和 P2（串口）patch 已应用（见 00.7 章节）
#==============================================================================

set -e
set -o pipefail

#==============================================================================
# 0. 配置区
#==============================================================================

AOSP_ROOT="/home/lechao/workspace/aosp"
KERNEL_SRC="/home/lechao/workspace/rpi5-kernel-build/common"
KERNEL_OUT="/home/lechao/workspace/rpi5-kernel-build/out/android_rpi5"
KERNEL_DEST="${AOSP_ROOT}/device/brcm/rpi5-kernel"
WINDOWS_IMG_DIR="/mnt/c/Files/RaspberryImages"

CLANG_BIN="/home/lechao/workspace/rpi5-kernel-build/prebuilts/clang/host/linux-x86/clang-r522817/bin"
CLANG_TRIPLE="aarch64-linux-gnu-"

LUNCH_TARGET="aosp_rpi5-bp1a-userdebug"
BUILD_JOBS=${BUILD_JOBS:-$(nproc)}
KERNEL_DEFCONFIG="android_rpi5_defconfig"
VERSION_PREFIX="RaspberryVanillaAOSP15"

# --- 锚点查找 REPO_ROOT + 接入维测库 ---------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }

# shellcheck source=../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init --with-errexit "mk_rpi5_full_image"

#==============================================================================
# 1. 参数解析与帮助
#==============================================================================

MODE=1  # 默认：全量编译

print_help() {
    cat << 'HELPEOF'
树莓派5 AOSP 一键编译打包脚本

用法: mk_rpi5_full_image.sh [-mode N] [-h]

  -mode 0  仅打包 — 不编译任何代码，直接用已有 boot/system/vendor 镜像
            通过 rpi5-mkimg.sh 生成 .img 并拷贝到 Windows 目录
            （约 3 分钟，适合镜像已就绪仅需重新打包分发的场景）

  -mode 1  全量编译 — 编译内核 + make bootimage+systemimage+vendorimage + 打包
            （默认模式，首次构建推荐，约 1-4 小时）

  -mode 2  仅内核 — 编译内核 + 拷贝到 AOSP + make bootimage + 打包
            仅重新打包内核和 dtb 到 boot.img，复用已有 system/vendor
            （约 10-25 分钟，★经常改内核时推荐★）

  -mode 3  仅 vendor — 仅 make vendorimage + 打包
            复用已有内核、boot.img 和 system.img
            （约 5-15 分钟增量，适合只改了 vendor 分区/HAL 层）

  -mode 4  仅 system — 仅 make systemimage + 打包
            复用已有内核、boot.img 和 vendor.img
            （约 5-20 分钟增量，适合只改了框架层/应用层）

  -h       显示此帮助信息

环境变量:
  BUILD_JOBS=N  自定义 make 并行数（默认 nproc，即 $(nproc)）
HELPEOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -mode)
            MODE="$2"
            if [[ ! "$MODE" =~ ^[0-4]$ ]]; then
                log_error "-mode 参数必须是 0~4"
                harness_exit 3
            fi
            shift 2
            ;;
        -h|--help)
            print_help
            harness_exit 0
            ;;
        *)
            log_error "未知参数: $1"
            log_error "用法: $0 [-mode N] [-h]，使用 -h 查看详细帮助"
            harness_exit 3
            ;;
    esac
done

# 根据 mode 确定构建范围
DO_KERNEL=false
DO_BOOT=false
DO_SYSTEM=false
DO_VENDOR=false

case $MODE in
    0)
        ;;
    1)
        DO_KERNEL=true
        DO_BOOT=true
        DO_SYSTEM=true
        DO_VENDOR=true
        ;;
    2)
        DO_KERNEL=true
        DO_BOOT=true
        ;;
    3)
        DO_VENDOR=true
        ;;
    4)
        DO_SYSTEM=true
        ;;
esac

# 构建计划描述（所有模式均为 4 个主步骤）
case $MODE in
    0) PLAN="仅打包已有镜像"     ;;
    1) PLAN="全量编译（内核 + AOSP + 打包）" ;;
    2) PLAN="内核编译 + bootimage + 打包" ;;
    3) PLAN="vendorimage + 打包" ;;
    4) PLAN="systemimage + 打包" ;;
esac

log_info "树莓派5 AOSP 一键编译打包"
log_info "模式: ${MODE} — ${PLAN}"
log_info "并行: ${BUILD_JOBS} 核心"

#==============================================================================
# 2. 编译内核（mode 1 或 mode 2）
#==============================================================================

if [ "$DO_KERNEL" = true ]; then
    step_begin "编译内核（AOSP Clang + LLD）"

    if [ ! -d "$KERNEL_SRC" ]; then
        log_error "内核源码目录不存在: $KERNEL_SRC"
        harness_exit 3
    fi
    if [ ! -d "$CLANG_BIN" ]; then
        log_error "Clang 工具链目录不存在: $CLANG_BIN"
        harness_exit 3
    fi

    log_info "内核源码: $KERNEL_SRC"
    log_info "编译输出: $KERNEL_OUT"

    export ARCH=arm64
    export PATH="${CLANG_BIN}:${PATH}"

    # 内核编译命令（使用 AOSP Clang + LLD 工具链）
    KERNEL_MAKE_CMD="make O=${KERNEL_OUT} \
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
        CLANG_TRIPLE=${CLANG_TRIPLE}"

    cd "$KERNEL_SRC"

    # 应用 defconfig（首次或配置变更时执行，增量编译时 .config 已存在则快速通过）
    log_info "应用内核配置: ${KERNEL_DEFCONFIG}"
    if ! ${KERNEL_MAKE_CMD} ${KERNEL_DEFCONFIG}; then
        log_error "defconfig 配置失败"
        harness_exit 1
    fi

    # 编译内核 Image + 设备树 dtb + overlays
    # Image：树莓派5 非压缩内核镜像
    # dtbs： 包含 bcm2712*.dtb（主设备树）和 overlays/*.dtbo（设备树覆盖）
    log_info "编译内核 Image + dtbs（预计 5-20 分钟）"
    if ! ${KERNEL_MAKE_CMD} Image dtbs -j${BUILD_JOBS}; then
        log_error "内核编译失败"
        harness_exit 1
    fi

    # 验证产物
    log_info "验证内核编译产物"
    KERNEL_IMAGE="${KERNEL_OUT}/arch/arm64/boot/Image"
    KERNEL_DTS_DIR="${KERNEL_OUT}/arch/arm64/boot/dts/broadcom"
    KERNEL_OVERLAYS_DIR="${KERNEL_DTS_DIR}/overlays"

    if [ ! -f "$KERNEL_IMAGE" ]; then
        log_error "内核镜像未生成: $KERNEL_IMAGE"
        harness_exit 3
    fi
    KERNEL_VER=$(strings "$KERNEL_IMAGE" | grep "Linux version" | head -1)
    echo "  [OK] Image ($(ls -lh "$KERNEL_IMAGE" | awk '{print $5}'))"
    echo "  [OK] 版本: $KERNEL_VER"

    for dtb in bcm2712-rpi-5-b.dtb bcm2712d0-rpi-5-b.dtb; do
        if [ ! -f "${KERNEL_DTS_DIR}/${dtb}" ]; then
            log_error "设备树未生成: ${dtb}"
            harness_exit 3
        fi
    done
    echo "  [OK] dtb 已生成"

    if [ -d "$KERNEL_OVERLAYS_DIR" ] && ls "${KERNEL_OVERLAYS_DIR}"/*.dtbo >/dev/null 2>&1; then
        echo "  [OK] overlays: $(ls "${KERNEL_OVERLAYS_DIR}"/*.dtbo 2>/dev/null | wc -l) 个"
    fi

    # 备份预编译内核 + 拷贝新内核
    log_info "同步内核产物到 AOSP（备份 + 拷贝 Image/dtb/overlays）"
    cd "$KERNEL_DEST"
    if [ ! -f "Image.prebuilt" ]; then
        cp Image Image.prebuilt 2>/dev/null || true
        cp bcm2712-rpi-5-b.dtb bcm2712-rpi-5-b.dtb.prebuilt 2>/dev/null || true
        cp bcm2712d0-rpi-5-b.dtb bcm2712d0-rpi-5-b.dtb.prebuilt 2>/dev/null || true
        cp -a overlays overlays.prebuilt 2>/dev/null || true
        echo "  [OK] 预编译内核已备份（.prebuilt），可通过 git checkout 恢复"
    fi

    # 拷贝新内核到 AOSP 预编译内核目录
    cp "$KERNEL_IMAGE" "$KERNEL_DEST/Image"
    cp "${KERNEL_DTS_DIR}"/bcm2712*.dtb "$KERNEL_DEST/"
    if [ -d "$KERNEL_OVERLAYS_DIR" ] && ls "${KERNEL_OVERLAYS_DIR}"/*.dtbo >/dev/null 2>&1; then
        rm -f "${KERNEL_DEST}/overlays/"*.dtbo 2>/dev/null || true
        cp "${KERNEL_OVERLAYS_DIR}"/*.dtbo "${KERNEL_DEST}/overlays/"
    fi
    echo "  [OK] Image / dtb / overlays 已同步到 device/brcm/rpi5-kernel/"

    step_end 0
else
    step_begin "跳过内核编译（使用已有内核）"

    # 确认预编译内核目录中有 Image（后续 make bootimage 依赖它）
    # mode 3/4 不编译 bootimage，但打包时需要已有 boot.img
    if [ "$DO_BOOT" = false ] && [ ! -f "${KERNEL_DEST}/Image" ]; then
        log_error "缺少预编译内核: ${KERNEL_DEST}/Image"
        log_error "请先运行 mode 1（全量编译）获取内核"
        harness_exit 3
    fi
    KERNEL_VER=$(strings "${KERNEL_DEST}/Image" | grep "Linux version" | head -1)
    log_info "使用已有内核: $KERNEL_VER"

    step_end 0
fi

#==============================================================================
# 3. 编译 AOSP 镜像
#==============================================================================

if [ "$DO_BOOT" = true ] || [ "$DO_SYSTEM" = true ] || [ "$DO_VENDOR" = true ]; then
    # 构建编译目标列表和步骤描述
    MAKE_TARGETS=""
    TARGET_DESC=""
    if [ "$DO_BOOT" = true ]; then
        MAKE_TARGETS="${MAKE_TARGETS} bootimage"
        TARGET_DESC="${TARGET_DESC} + bootimage"
    fi
    if [ "$DO_SYSTEM" = true ]; then
        MAKE_TARGETS="${MAKE_TARGETS} systemimage"
        TARGET_DESC="${TARGET_DESC} + systemimage"
    fi
    if [ "$DO_VENDOR" = true ]; then
        MAKE_TARGETS="${MAKE_TARGETS} vendorimage"
        TARGET_DESC="${TARGET_DESC} + vendorimage"
    fi
    TARGET_DESC="${TARGET_DESC# + }"  # 去掉开头的 " + "

    step_begin "编译 AOSP 镜像（${TARGET_DESC}）"

    # 初始化构建环境
    cd "$AOSP_ROOT"
    if [ ! -f "build/envsetup.sh" ]; then
        log_error "AOSP 构建脚本不存在: $AOSP_ROOT/build/envsetup.sh"
        harness_exit 3
    fi

    log_info "source build/envsetup.sh && lunch ${LUNCH_TARGET}"
    source build/envsetup.sh
    lunch "$LUNCH_TARGET"

    if [ -z "${ANDROID_PRODUCT_OUT}" ]; then
        log_error "ANDROID_PRODUCT_OUT 未设置，lunch 可能失败"
        harness_exit 3
    fi
    log_info "产物目录: ${ANDROID_PRODUCT_OUT}"

    log_info "make ${MAKE_TARGETS} -j${BUILD_JOBS}"

    if ! make ${MAKE_TARGETS} -j${BUILD_JOBS}; then
        log_error "AOSP 编译失败"
        log_error "排查建议：1.磁盘空间 df -h  2.内存/swap free -h  3.减少并行 BUILD_JOBS=4 $0 -mode ${MODE}"
        harness_exit 1
    fi

    log_info "验证编译产物"
    # 验证本次编译的镜像
    for img in ${MAKE_TARGETS}; do
        img_file="${img}image"
        # systemimage -> system.img, vendorimage -> vendor.img, bootimage -> boot.img
        case "$img" in
            bootimage)    img_file="boot.img" ;;
            systemimage)  img_file="system.img" ;;
            vendorimage)  img_file="vendor.img" ;;
        esac
        if [ ! -f "${ANDROID_PRODUCT_OUT}/${img_file}" ]; then
            log_error "缺少镜像: ${img_file}"
            harness_exit 3
        fi
        echo "  [OK] ${img_file} ($(ls -lh "${ANDROID_PRODUCT_OUT}/${img_file}" | awk '{print $5}'))"
    done

    # 验证打包所需的全部镜像均存在
    log_info "验证打包所需镜像完整性"
    MISSING_IMGS=""
    for img in boot.img system.img vendor.img; do
        if [ ! -f "${ANDROID_PRODUCT_OUT}/${img}" ]; then
            MISSING_IMGS="${MISSING_IMGS} ${img}"
        fi
    done
    if [ -n "$MISSING_IMGS" ]; then
        log_error "打包所需镜像缺失:${MISSING_IMGS}"
        log_error "请先运行 mode 1（全量编译）生成缺失镜像"
        harness_exit 3
    fi
    echo "  [OK] boot.img / system.img / vendor.img 均已就绪"

    step_end 0

else
    step_begin "确认镜像就绪（跳过编译，仅验证 .img 文件存在）"

    # mode 0 也需要 lunch 以设置 rpi5-mkimg.sh 依赖的环境变量
    cd "$AOSP_ROOT"
    source build/envsetup.sh
    lunch "$LUNCH_TARGET"

    # 确认三个 .img 都存在
    for img in boot.img system.img vendor.img; do
        if [ ! -f "${ANDROID_PRODUCT_OUT}/${img}" ]; then
            log_error "缺少镜像: ${img}"
            log_error "请先运行 mode 1（全量编译）生成镜像"
            harness_exit 3
        fi
        echo "  [OK] ${img} 已就绪"
    done
    log_info "使用已有镜像（跳过编译）"

    step_end 0
fi

#==============================================================================
# 4. 打包完整刷机镜像（rpi5-mkimg.sh）
#==============================================================================

step_begin "生成可刷写 .img 镜像（rpi5-mkimg.sh）"

cd "$AOSP_ROOT"

if [ ! -f "./rpi5-mkimg.sh" ]; then
    log_error "rpi5-mkimg.sh 不存在: $AOSP_ROOT/rpi5-mkimg.sh"
    harness_exit 3
fi

# 删除旧的刷机包（rpi5-mkimg.sh 遇到同名文件会报错退出）
log_info "清理旧的 ${VERSION_PREFIX}-*-rpi5.img"
OLD_IMGS=$(ls "${ANDROID_PRODUCT_OUT}/${VERSION_PREFIX}"-*-rpi5.img 2>/dev/null || true)
if [ -n "$OLD_IMGS" ]; then
    echo "$OLD_IMGS" | while read -r old_img; do
        log_info "删除: $(basename "$old_img")"
        rm -f "$old_img"
    done
else
    log_info "无旧镜像需要清理"
fi

# 运行 rpi5-mkimg.sh（需要 sudo 进行分区/格式化/losetup）
# 注意：sudo 默认会清空环境变量，必须显式传递 TARGET_PRODUCT 和 ANDROID_PRODUCT_OUT
log_info "运行 rpi5-mkimg.sh（需要 sudo 权限）"
if ! sudo TARGET_PRODUCT="${TARGET_PRODUCT}" ANDROID_PRODUCT_OUT="${ANDROID_PRODUCT_OUT}" ./rpi5-mkimg.sh; then
    log_error "rpi5-mkimg.sh 执行失败"
    log_error "常见原因：1.sudo 权限不足  2.磁盘不足（df -h 确认至少 20GB）  3.loop 设备不可用（sudo modprobe loop）"
    harness_exit 1
fi

# 验证生成的新镜像
log_info "验证生成的刷机包"
NEW_IMG=$(ls -t "${ANDROID_PRODUCT_OUT}/${VERSION_PREFIX}"-*-rpi5.img 2>/dev/null | head -1 || true)
if [ -z "$NEW_IMG" ]; then
    log_error "刷机包未生成"
    harness_exit 3
fi
IMG_SIZE=$(ls -lh "$NEW_IMG" | awk '{print $5}')
IMG_NAME=$(basename "$NEW_IMG")
echo "  [OK] ${IMG_NAME} (${IMG_SIZE})"

step_end 0

#==============================================================================
# 5. 拷贝到 Windows 目录
#==============================================================================

step_begin "拷贝刷机包到 ${WINDOWS_IMG_DIR}"

if [ ! -d "$WINDOWS_IMG_DIR" ]; then
    mkdir -p "$WINDOWS_IMG_DIR"
fi

# 保留最近 2 个版本，删除更旧的
OLD_COUNT=$(ls "${WINDOWS_IMG_DIR}/${VERSION_PREFIX}"-*-rpi5.img 2>/dev/null | wc -l || true)
if [ "$OLD_COUNT" -gt 0 ]; then
    KEEP=2
    ls -t "${WINDOWS_IMG_DIR}/${VERSION_PREFIX}"-*-rpi5.img 2>/dev/null \
        | tail -n +$((KEEP+1)) \
        | while read -r old; do
            log_info "删除旧镜像: $(basename "$old")"
            rm -f "$old"
        done
    DELETED=$((OLD_COUNT > KEEP ? OLD_COUNT - KEEP : 0))
    log_info "保留最近 ${KEEP} 个，清理 ${DELETED} 个旧镜像"
fi

if ! cp "$NEW_IMG" "$WINDOWS_IMG_DIR/"; then
    log_error "镜像拷贝失败，检查 /mnt/c/ 是否已挂载"
    harness_exit 1
fi
echo "  [OK] ${IMG_NAME} → ${WINDOWS_IMG_DIR}/"

step_end 0

#==============================================================================
# 6. 完成
#==============================================================================

echo ""
echo "=========================================="
echo " 一键编译打包完成（mode ${MODE}）"
echo "=========================================="
echo ""
echo "产物信息："
echo "  模式:     ${MODE} — ${PLAN}"
echo "  刷机包:   ${WINDOWS_IMG_DIR}/${IMG_NAME}"
echo "  大小:     ${IMG_SIZE}"
echo "  内核:     ${KERNEL_VER}"
echo ""
echo "下一步："
echo "  1. Windows 打开 C:\\Files\\RaspberryImages\\"
echo "  2. 用 Raspberry Pi Imager 或 balenaEtcher 写入 SD 卡"
echo ""

# 记录构建报告到 artifact（替代原 build_history.txt）
BUILD_END_TS=$(date +%s)
BUILD_DURATION=$((BUILD_END_TS - _H_INIT_TS))
REPORT_FILE=$(mktemp /tmp/build-report.XXXXXX.json)
cat > "$REPORT_FILE" <<EOF
{
  "ts": "$(_h_ts_iso)",
  "script": "mk_rpi5_full_image",
  "mode": ${MODE},
  "plan": "${PLAN}",
  "exit_code": 0,
  "duration_sec": ${BUILD_DURATION},
  "env": {
    "BUILD_JOBS": ${BUILD_JOBS},
    "AOSP_ROOT": "${AOSP_ROOT}",
    "KERNEL_SRC": "${KERNEL_SRC}",
    "LUNCH_TARGET": "${LUNCH_TARGET}"
  },
  "artifacts": {
    "image": "${IMG_NAME:-unknown}",
    "image_size": "${IMG_SIZE:-unknown}",
    "kernel_version": "${KERNEL_VER:-unknown}",
    "windows_dest": "${WINDOWS_IMG_DIR}"
  }
}
EOF
artifact_register "$REPORT_FILE" "build-report.json"
rm -f "$REPORT_FILE"

harness_exit 0

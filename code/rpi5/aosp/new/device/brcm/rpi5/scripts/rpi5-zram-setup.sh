#!/system/bin/sh
# ============================================================
# rpi5-zram-setup.sh — 开机启用 zram0 换入设备（方向三批一）
# 所属：rpi5 设备 init 脚本（system 分区，root oneshot 运行）
#
# 职责（boot-time，root）：
#   1) 按 MemTotal 四成自适应计算 zram0 disksize；
#   2) comp_algorithm 置 zstd（压缩率高、CPU 开销可接受）；
#   3) 枚举 removable 标志为 1 的 USB 块设备（/sys/block/sd*）作 zram0 写回
#      后备（backing_dev），令 idle 换出页可回写到 U 盘；无 U 盘则跳过，
#      退化为纯内存 zram（不影响 swap 启用）；
#   4) mkswap + swapon 启用换入；标记全部页 idle 并触发一次 writeback（预研）。
#
# 失败不影响启动：任一步失败即回退——swapoff 并复位 zram0（reset 同时清除
#   backing_dev 绑定），脚本恒以 exit 0 结束，绝不阻断 init boot 流程。
# 幂等：已启用（disksize>0 且在 /proc/swaps 内）时直接跳过，不重复 mkswap。
#
# 安全边界：zram0 为内存压缩块设备（无物理介质）；写回后备仅选 removable==1
#   的 sd* USB 块设备（可移除测试介质），严禁选系统盘 mmcblk（系统根/SD 卡）。
#   backing_dev 以裸块写回 U 盘，不触碰系统盘与 SD 卡。
# ============================================================

ZRAM=/sys/block/zram0
DEV=/dev/block/zram0
TAG=lechao_zram_setup

# 内核日志留痕（logcat 早期不可用，用 /dev/kmsg 供 dmesg 核查）
kmsg() {
    echo "$TAG: $1" > /dev/kmsg 2>/dev/null
}

# 复位 zram0：内核要求改 disksize 前无活动用户；仅写 0 不彻底复位（实测二次
#   写 disksize 会 EBUSY），须写 reset（无该属性时退化为写 0）。reset 经
#   zram_reset_device → reset_bdev 同时释放 backing_dev 绑定。
reset_zram() {
    echo 1 > "$ZRAM/reset" 2>/dev/null || echo 0 > "$ZRAM/disksize" 2>/dev/null
}

# 枚举 removable 标志为 1 的 USB 块设备：仅扫 /sys/block/sd*（USB 存储命名），
#   命中即打印 /dev/block/<name>；无命中打印空串（跳过写回，退化为纯内存 zram）。
#   严禁 mmcblk（系统盘）：不匹配 sd* 前缀，天然排除；removable==0 的固定
#   USB SSD（如 sda）亦跳过，只取可移除测试介质。
find_usb_backing_dev() {
    for sb in /sys/block/sd*; do
        [ -e "$sb/removable" ] || continue
        if [ "$(cat "$sb/removable" 2>/dev/null)" = "1" ]; then
            echo "/dev/block/${sb##*/}"
            return 0
        fi
    done
    echo ""
}

# 回退：禁 swap + 复位 zram0（reset 同时清除 backing_dev 绑定），恒 exit 0
#   （不影响启动）
fail() {
    kmsg "setup failed: $1 -> rollback (swapoff + reset/clear backing_dev)"
    swapoff "$DEV" 2>/dev/null
    reset_zram
    exit 0
}

# 幂等：swap 表已含本设备即视为启用，直接跳过（避免重复 mkswap 破坏在用 swap）。
# 注：设备 sh 的 [ -gt ] 亦为 32 位（disksize 字节数可达 3.3e9 溢出为负），故
#   一律以 /proc/swaps 存在性判据，不用数值比较。
if grep -q "$DEV" /proc/swaps 2>/dev/null; then
    kmsg "already active (in /proc/swaps), skip"
    exit 0
fi

# MemTotal（kB）→ 四成 → 字节。缺读即回退（不猜测内存大小）。
# 注：设备 /system/bin/sh 的 $(( )) 为 32 位算术，kB*40/100*1024 会溢出为负
#   （RPi5 8132136kB 实测得 -964044800），故用 toybox awk 双精度整数式求值
#   （中间值 < 2^53，精确）；再经数字校验（CXX-003 输入防御）防异常值写 sysfs。
memtotal_kb=$(sed -n 's/^MemTotal:[[:space:]]*\([0-9][0-9]*\) kB.*/\1/p' /proc/meminfo)
if [ -z "$memtotal_kb" ]; then
    fail "MemTotal unreadable from /proc/meminfo"
fi
disksize=$(awk -v m="$memtotal_kb" 'BEGIN { printf "%.0f", m * 40 / 100 * 1024 }')
case "$disksize" in
    ''|*[!0-9]*) fail "disksize compute failed (MemTotal=${memtotal_kb}kB got='$disksize')" ;;
esac
# 全十进制且非 "0" 即有效（awk printf %.0f 无前导零；不做 32 位数值比较）
[ "$disksize" != "0" ] || fail "disksize computed zero (MemTotal=${memtotal_kb}kB)"

# 配置算法与容量（先复位确保可写；sysfs 写失败即回退）
reset_zram
echo zstd > "$ZRAM/comp_algorithm" 2>/dev/null

# 写回后备（backing_dev）：必须在 disksize 之前绑定——内核 init_done 后
#   写 backing_dev 返回 EBUSY。仅 U 盘在位时绑定；无 U 盘跳过（纯内存 zram，
#   不影响 swap 启用）。
usb_dev=$(find_usb_backing_dev)
if [ -n "$usb_dev" ]; then
    if echo "$usb_dev" > "$ZRAM/backing_dev" 2>/dev/null; then
        kmsg "backing_dev bound: $usb_dev"
    else
        kmsg "backing_dev bind failed ($usb_dev), continue memory-only"
    fi
else
    kmsg "no removable USB block device, memory-only zram"
fi

echo "$disksize" > "$ZRAM/disksize" 2>/dev/null || fail "write disksize=$disksize"
# disksize 回读校验：内核按内部粒度对齐回读值（可能与写入值不同），故只校验
#   非空且为非零十进制（字符串判据，避 32 位 [ -gt ] 溢出与对齐误判）
cur=$(cat "$ZRAM/disksize" 2>/dev/null)
case "$cur" in
    ''|*[!0-9]*) fail "disksize readback invalid (cur='$cur')" ;;
esac
[ "$cur" != "0" ] || fail "disksize readback zero (want=$disksize)"

# 建立 swap 签名并启用
mkswap "$DEV" >/dev/null 2>&1 || fail "mkswap $DEV"
swapon "$DEV" 2>/dev/null || fail "swapon $DEV"

# 写回预研：标记全部页 idle 并触发一次 writeback（仅 U 盘后备在位时；开机
#   初期通常无换出页，writeback 为 no-op，不判失败——页随时间换出后由
#   daemon 采集 bd_stat 观测写回量）。best-effort，不影响 swap 生效。
if [ -n "$usb_dev" ]; then
    echo all > "$ZRAM/idle" 2>/dev/null
    echo idle > "$ZRAM/writeback" 2>/dev/null
fi

kmsg "enabled: disksize=${disksize}B (40% of ${memtotal_kb}kB) algo=zstd backing=${usb_dev:-none}"
exit 0

#!/system/bin/sh
# ============================================================
# rpi5-zram-setup.sh — 开机启用 zram0 换入设备（方向三批一）
# 所属：rpi5 设备 init 脚本（system 分区，root oneshot 运行）
#
# 职责（boot-time，root）：
#   1) 按 MemTotal 四成自适应计算 zram0 disksize；
#   2) comp_algorithm 置 zstd（压缩率高、CPU 开销可接受）；
#   3) mkswap + swapon 启用换入。
#
# 失败不影响启动：任一步失败即回退——swapoff 并清零 disksize（禁 swap），
#   脚本恒以 exit 0 结束，绝不阻断 init boot 流程。
# 幂等：已启用（disksize>0 且在 /proc/swaps 内）时直接跳过，不重复 mkswap。
#
# 安全边界：仅操作 zram0（内存压缩块设备，无物理介质），不触碰任何物理
#   块设备 / SD 卡 / USB 盘，无破坏性写。
# ============================================================

ZRAM=/sys/block/zram0
DEV=/dev/block/zram0
TAG=lechao_zram_setup

# 内核日志留痕（logcat 早期不可用，用 /dev/kmsg 供 dmesg 核查）
kmsg() {
    echo "$TAG: $1" > /dev/kmsg 2>/dev/null
}

# 复位 zram0：内核要求改 disksize 前无活动用户；仅写 0 不彻底复位（实测二次
#   写 disksize 会 EBUSY），须写 reset（无该属性时退化为写 0）。
reset_zram() {
    echo 1 > "$ZRAM/reset" 2>/dev/null || echo 0 > "$ZRAM/disksize" 2>/dev/null
}

# 回退：禁 swap + 复位（禁 swap），恒 exit 0（不影响启动）
fail() {
    kmsg "setup failed: $1 -> rollback (swapoff + reset)"
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

kmsg "enabled: disksize=${disksize}B (40% of ${memtotal_kb}kB) algo=zstd"
exit 0

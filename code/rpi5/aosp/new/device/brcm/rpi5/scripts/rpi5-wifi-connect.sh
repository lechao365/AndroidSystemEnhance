#!/system/bin/sh
#
# rpi5_wifi_connect - 开机自动读取 wifi.conf 连接指定 WiFi（DHCP）
#
# 配置文件格式 (INI，位于 SD 卡 boot 分区根目录 wifi.conf):
#   ssid = HUAWEI-BE7P
#   psk  = YourPassword
#   key_mgmt = WPA-PSK      (可选，默认 WPA2-PSK；开放网络设 NONE，WPA3 设 SAE)
#
# 行为（daemon 模式，init 去掉 oneshot 后常驻）：
#   1. 等 sys.boot_completed 后由 init 触发
#   2. 挂载 SD 卡 boot 分区到 /data/boot，读取 wifi.conf
#   3. 等待 WifiService 就绪（最多 60s），显式开启 WiFi
#   4. 无限循环监控：未连接则 connect-network，已连接则保持

CONF=/data/boot/wifi.conf
LOG_TAG=rpi5_wifi

logi() { log -t "$LOG_TAG" -p i "$*"; }
loge() { log -t "$LOG_TAG" -p e "$*"; }

# 挂载 SD 卡 boot 分区到 /data/boot（/data 可写；根分区只读不能创建 /boot）
# 用户始终在 SD 卡 boot 分区编辑 wifi.conf，脚本内部挂载点对用户透明
BOOT_MNT=/data/boot
if [ ! -d "$BOOT_MNT" ]; then
    mkdir -p "$BOOT_MNT"
fi
if ! mountpoint -q "$BOOT_MNT" 2>/dev/null; then
    if ! mount -t vfat /dev/block/mmcblk0p1 "$BOOT_MNT" 2>/dev/null; then
        loge "mount $BOOT_MNT failed, abort"
        exit 1
    fi
fi
logi "boot partition mounted at $BOOT_MNT"

# 配置文件不存在则静默退出（用户可选启用此功能）
if [ ! -f "$CONF" ]; then
    logi "no $CONF found, skip wifi auto-connect"
    exit 0
fi

# 解析 INI 字段（兼容带/不带引号、前后空格、注释）
parse_field() {
    grep -iE "^[[:space:]]*$1[[:space:]]*=" "$CONF" | head -1 \
        | sed -E 's/^[^=]*=[[:space:]]*//; s/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/'
}

ssid=$(parse_field ssid)
psk=$(parse_field psk)
km=$(parse_field key_mgmt)

if [ -z "$ssid" ]; then
    loge "ssid is empty in $CONF"
    exit 1
fi

# 默认配置检测：用户未修改 wifi.conf 时，提示并跳过（避免盲目连接名为 "default" 的网络）
if [ "$ssid" = "default" ] || [ "$psk" = "default" ]; then
    loge "wifi.conf uses default values! Edit wifi.conf on SD card boot partition with your real WiFi name and password, then reboot"
    exit 0
fi

# 默认 WPA2-PSK（覆盖 99% 家用路由器场景）
if [ -z "$km" ]; then
    km="WPA-PSK"
fi

logi "config: ssid=$ssid, key_mgmt=$km"

# 等待 WifiService 就绪（cmd wifi status 可执行）
i=0
while [ $i -lt 30 ]; do
    if cmd wifi status >/dev/null 2>&1; then
        break
    fi
    i=$((i + 1))
    sleep 2
done

if [ $i -ge 30 ]; then
    loge "WifiService not ready after 60s, abort"
    exit 1
fi

# 显式开启 WiFi（不依赖 overlay def_wifi_on，避免 userdata 旧数据覆盖默认值）
cmd wifi set-wifi-enabled enabled >/dev/null 2>&1
sleep 2
logi "wifi enabled explicitly"

# 根据 key_mgmt 选择 connect-network 参数
connect_wifi() {
    case "$km" in
        NONE|none|OPEN|open)
            cmd wifi connect-network "$ssid" nosuppress >/dev/null 2>&1
            ;;
        SAE|sae|WPA3*|wpa3*)
            # WPA3，使用 sae 参数（Android 15 cmd wifi 支持）
            cmd wifi connect-network "$ssid" sae "$psk" >/dev/null 2>&1
            ;;
        *)
            # WPA-PSK / WPA2-PSK（默认）
            cmd wifi connect-network "$ssid" wpa2 "$psk" >/dev/null 2>&1
            ;;
    esac
}

# 检查是否已连上目标 SSID
# 用 case 做 SSID 后缀匹配，避免 grep 正则对特殊字符（- 等）的误解析
is_connected() {
    local cur
    cur=$(cmd wifi status 2>/dev/null | grep 'connected to' | head -1 | cut -d'"' -f2)
    case "$cur" in
        *"$ssid"*) return 0 ;;
        *) return 1 ;;
    esac
}

# connect_wifi 失败计数（避免无限重试刷日志）
_connect_fail_count=0

# daemon 循环：监控 WiFi 连接状态，掉线则重连
logi "entering daemon mode (monitoring wifi connection)"
while true; do
    if is_connected; then
        _connect_fail_count=0
    else
        # 未连接，尝试连接（连续失败超过 30 次后降低频率到 5 分钟一次，避免刷日志）
        if [ "$_connect_fail_count" -lt 30 ]; then
            connect_wifi
            sleep 5
            if is_connected; then
                logi "connected to $ssid"
                _connect_fail_count=0
            else
                _connect_fail_count=$((_connect_fail_count + 1))
                [ "$_connect_fail_count" -eq 30 ] && loge "connect failed 30 times, reducing retry frequency to 5 minutes"
            fi
        else
            # 失败次数过多，降低频率（每 5 分钟重试一次）
            connect_wifi
            if is_connected; then
                logi "connected to $ssid (after extended retry)"
                _connect_fail_count=0
            fi
            sleep 270  # 270 + 后续 30 = 300s = 5min
        fi
    fi
    sleep 30  # 每 30 秒检查一次
done

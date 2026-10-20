#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" != "--confirm" ]; then
  echo "error: 需要 --confirm 才允许刷写" >&2
  exit 2
fi
if [ "${LC_PRIVILEGE_ALLOW_DESTRUCTIVE:-}" != "YES" ]; then
  echo "error: 需要 LC_PRIVILEGE_ALLOW_DESTRUCTIVE=YES（helper 破坏性确认）" >&2
  exit 2
fi

DEVICE="${LC_FLASH_DEVICE:-}"
IMAGE="${LC_FLASH_IMAGE:-}"
PRODUCT_OUT="${ANDROID_PRODUCT_OUT:-}"
if [ -z "$DEVICE" ] || [ -z "$IMAGE" ] || [ -z "$PRODUCT_OUT" ]; then
  echo "error: LC_FLASH_DEVICE/LC_FLASH_IMAGE/ANDROID_PRODUCT_OUT 必须设置" >&2
  exit 2
fi
if [[ ! "$DEVICE" =~ ^/dev/sd[a-z][0-9]+$ ]]; then
  echo "error: 只允许 /dev/sdXn 分区: $DEVICE" >&2
  exit 2
fi
if [ ! -f "$IMAGE" ]; then
  echo "error: 镜像不存在: $IMAGE" >&2
  exit 2
fi
if ! IMAGE_REAL=$(readlink -f -- "$IMAGE"); then
  echo "error: 无法解析镜像路径: $IMAGE" >&2
  exit 2
fi
if ! PRODUCT_REAL=$(readlink -f -- "$PRODUCT_OUT"); then
  echo "error: 无法解析 ANDROID_PRODUCT_OUT: $PRODUCT_OUT" >&2
  exit 2
fi
case "$IMAGE_REAL" in
  "$PRODUCT_REAL"/*) ;;
  *) echo "error: 镜像不在 ANDROID_PRODUCT_OUT 内: $IMAGE_REAL" >&2; exit 2 ;;
esac
RM=$(lsblk -no RM "$DEVICE" 2>/dev/null | tr -d '[:space:]' || true)
if [ "$RM" != "1" ]; then
  echo "error: 目标不是可移动设备（removable != 1）: $DEVICE" >&2
  exit 2
fi

if ! IMAGE_SIZE=$(stat -c %s -- "$IMAGE_REAL" 2>/dev/null); then
  echo "error: 无法读取镜像大小: $IMAGE_REAL" >&2
  exit 2
fi
case "$IMAGE_SIZE" in
  ''|*[!0-9]*) echo "error: 镜像大小非法: $IMAGE_SIZE" >&2; exit 2 ;;
esac
if ! DEVICE_SIZE=$(blockdev --getsize64 -- "$DEVICE" 2>/dev/null); then
  echo "error: 无法读取目标设备容量: $DEVICE" >&2
  exit 2
fi
case "$DEVICE_SIZE" in
  ''|*[!0-9]*) echo "error: 目标设备容量非法: $DEVICE_SIZE" >&2; exit 2 ;;
esac
if [ "$IMAGE_SIZE" -gt "$DEVICE_SIZE" ]; then
  echo "error: 镜像大小 $IMAGE_SIZE 超过目标分区容量 $DEVICE_SIZE: $DEVICE" >&2
  exit 2
fi

echo "target=$DEVICE image=$IMAGE_REAL" >&2
dd if="$IMAGE_REAL" of="$DEVICE" bs=4M conv=fsync status=progress
sync

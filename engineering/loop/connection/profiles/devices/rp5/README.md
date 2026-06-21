# Raspberry Pi 5 Profile

> 关联设计：`docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md`

## 设备
- 设备型号：Raspberry Pi 5
- 连接方式：UART 串口（bootstrap）+ 网络 ADB（feature）

## 当前 profile

- `default.json`：`transport=serial`，用于 boot / bootstrap / fallback
- `adb.json`：`transport=adb`，用于 feature suite 与 adb shell 验收

## 串口参数
- baudrate：115200
- 数据位 / 停止位 / 校验：8N1

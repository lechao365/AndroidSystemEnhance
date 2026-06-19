# Raspberry Pi 5 Profile

> **状态**：占位骨架，具体 profile 字段在 rp5-serial provider MVP 实现时回填

## 设备

- 设备型号：Raspberry Pi 5
- 连接方式：UART 串口

## 串口参数（占位）

- baudrate：115200（占位，具体值待实现时确认）
- 数据位 / 停止位 / 校验：待确认

## 语义标记（占位）

- prompt marker：待回填（shell 提示符特征）
- boot marker：待回填（启动阶段标志）
- boot timeout：待回填
- reboot loop 阈值：待回填

## 回填时机

以上字段在 `connection/providers/rp5-serial/` MVP 实现并真机联调后，根据实际观察结果回填到本文件或独立配置文件。

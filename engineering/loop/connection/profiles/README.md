# Connection Profiles

存放 provider/device 配置语义，描述「如何理解这台板子」。

## 范围

本目录承载**设备语义配置**，不承载 provider 运行配置（COM 口、baudrate、listen address 等运行参数由 provider 自身管理）：

- prompt marker（shell 提示符特征）
- boot marker（启动阶段标志）
- line ending（行结束符）
- boot timeout（启动超时阈值）
- reboot loop 阈值
- rule 参数
- workflow override

## 配置优先级

建议按以下顺序覆盖（后者覆盖前者）：

1. provider 默认配置
2. 设备 profile（如 RPi5）
3. workflow override

## 目录结构

| 目录 | 说明 |
|------|------|
| [devices/](./devices/) | 按设备组织 profile，当前仅 `rp5/` |

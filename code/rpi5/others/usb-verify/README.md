# usb-verify

USB 故障注入验证工具（fault-verify），用于设备端 USB 链路故障模拟与统计校验。

## 构建

依赖 gcc 与 make，在工具目录下直接执行 `make` 即可生成 `fault-verify` 可执行文件，
产物可直接拷贝到目标设备运行；需要清理时执行 `make clean`。
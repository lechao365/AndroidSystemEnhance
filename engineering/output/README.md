# engineering/output/

工程维测与运行产物统一目录。所有脚本运行时产生的日志和产物均落入此目录。

## host-log/

rp5-serial Windows Host 进程的运行产物，包括：
- 串口 transcript 文件（rp5-serial-transcript.log）
- Host 进程日志（rp5_serial_host.log）

由 Windows 端 `start_rp5_serial_host.bat` 触发写入。

## log/

WSL2 端 harness 脚本统一运行日志，由 `harness_observability.sh` 管理：
- 每脚本独立子目录：`<script-name>/<ts>.log` + `latest.log`
- 中间产物：`<script-name>/artifacts/<ts>-<name>`
- 自动轮转：保留最近 3 份

## runs/

LE（Loop Engineering）框架运行产物：
- 按时间戳命名的运行目录：`<ts>-<scenario>/`
- 结构化报告：`baseline/report.json`
- 人工摘要：`baseline/summary.txt`

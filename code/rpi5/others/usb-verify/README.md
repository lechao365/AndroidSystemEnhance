# usb-verify

USB 故障注入验证工具（fault-verify），用于设备端 USB 链路故障模拟与统计校验。

## 构建

依赖 gcc 与 make，在工具目录下直接执行 `make` 即可生成 `fault-verify` 可执行文件，
产物可直接拷贝到目标设备运行；需要清理时执行 `make clean`。

## --expect-json：消费 usb-fault-inject 的期望 JSON

`--expect-json <path>` 读取 rpi-zero2w 侧 `usb-fault-inject` 由
`expect_output_by_id()` 输出的单行 JSON，并映射为本次校验的期望：

```
{"fault":"<name>","expect":{"kernel_event":<n>,
  "error_count":<n>,"reset_count":<n>,"stall_count":<n>,
  "corrupt_count":<n>,"timeout_count":<n>}}
```

- `kernel_event` → 事件类型（数值与内核 `VENDOR_LECHAO_USBD_EVENT_*` 枚举 0..6 一致）；
- `error/reset/stall/corrupt/timeout_count` → 对应 `--*-ge` 阈值（`-1` 表示不校验，`>=0` 表示期望 `actual >= N`，与注入器语义一致）。

优先级：**命令行显式阈值优先**，`--expect-json` 只填空缺（未被命令行设置的字段）。
文件不可读、JSON 非法、字段越界或缺少 `fault`/`expect`/`kernel_event` 时按参数错误退出（`FV_ERR_ARGS=1`）。

示例：

```
fault-verify check stats --expect-json /tmp/expect-stall-in.json
fault-verify check event --expect-json /tmp/expect-timeout.json
```

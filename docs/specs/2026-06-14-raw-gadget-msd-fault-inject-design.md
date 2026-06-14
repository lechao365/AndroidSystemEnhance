# Design: raw-gadget MSD 故障注入工具重写

**Date**: 2026-06-14
**Scope**: `patchs/rpi-zero2w/others/usb-fault-inject/`
**Status**: Approved

## 1. 问题背景

当前 `usb-fault-inject` 工具的 Device 侧（raw-gadget 封装）存在根本性架构缺陷：

1. **缺少完整 gadget 生命周期**：只做了 `INIT`，缺少 `RUN`、`EVENT_FETCH` 枚举循环、`EP0` 控制传输响应、`EP_ENABLE` 端点使能
2. **端点 handle 语义错误**：用硬编码端点地址 `0x81`/`0x02` 当作 raw-gadget handle，实际应为 `EP_ENABLE` 返回的数组索引
3. **UDC 配置错误**：`DEFAULT_UDC` 写成 `"fe980000.usb"`（Pi 4 地址），Pi Zero 2W 应为 `"20980000.usb"`
4. **缺少 USB 描述符**：raw-gadget 不自带描述符，必须自己构造 Device/Config/Interface/Endpoint/String 描述符
5. **缺少 MSD 协议处理**：没有 SCSI 命令应答，Host 无法识别设备

这导致整个 Device 侧工具当前处于**不可运行**的骨架状态。

## 2. 设计目标

- 完全接管 UDC，实现完整的 USB MSD Device（不依赖 g_mass_storage）
- 支持对 USB Mass Storage Bulk-Only Transport (BOT) 协议全环节故障注入
- Device 侧自带 64MB 内存盘后端，Host 可正常识别 `/dev/sdX` 并读写
- 最终支持 11 种可独立注入的故障类型

## 3. 故障列表变更

| ID | 名称 | 变更 | 原因 |
|----|------|------|------|
| F4 | corrupt-cbw-sig | **删除** | CBW 是 Host→Device 方向，Device 无法修改 Host 发出的 CBW signature；Host 端也不校验自己发出的 CBW sig |
| F9 | abort | **重定义为 STALL+TIMEOUT** | USB 2.0 无 Bulk ERR PID；原实现与 F1 STALL 完全等价。重定义为"STALL IN 端点后不响应直到超时"，让 Host 走 abort+reset 双路径 |

最终 11 种故障：F1(stall-in), F2(stall-out), F3(timeout), F5(csw-sig), F6(csw-tag), F7(csw-status), F8(short), F9(abort=stall+timeout), F10(hotplug), F11(disconnect), F12(degrade)

## 4. 架构设计

### 4.1 四层架构

```
┌──────────────────────────────────────────────────┐
│ Layer 4: Fault Injection Engine (faults.c)        │
│   11 种故障，在 BOT 层钩子点注入                   │
├──────────────────────────────────────────────────┤
│ Layer 3: SCSI Command Dispatcher (scsi.c)         │
│   INQUIRY/TUR/READ_CAP/SENSE/MODE_SENSE/          │
│   PREVENT/START_STOP/READ10/WRITE10               │
│   64MB 内存盘后端 (malloc + memset)               │
├──────────────────────────────────────────────────┤
│ Layer 2: BOT State Machine (bot.c)                │
│   CBW接收 → SCSI分发 → Data中转 → CSW构造         │
│   故障注入钩子在 CBW后/Data后/CSW前                │
├──────────────────────────────────────────────────┤
│ Layer 1: raw-gadget Core (raw-gadget.c)           │
│   INIT → RUN → EVENT_FETCH 枚举循环               │
│   EP0 控制传输 (描述符/class request)              │
│   EP_ENABLE → handle 管理                          │
├──────────────────────────────────────────────────┤
│ Layer 0: /dev/raw-gadget ioctl 包装               │
└──────────────────────────────────────────────────┘
```

### 4.2 文件结构

```
patchs/rpi-zero2w/others/usb-fault-inject/
├── Makefile              # 修改：新增源文件
├── main.c                # 修改：UDC地址修正，调用流程调整
├── raw-gadget.h          # 修改：新增接口声明
├── raw-gadget.c          # 重写：完整枚举 + EP0 处理
├── raw-gadget-internal.h # 新增：内部状态共享
├── usb-descriptors.c     # 新增：USB 描述符定义
├── usb-descriptors.h     # 新增
├── scsi.c                # 新增：SCSI 命令处理 + 内存盘
├── scsi.h                # 新增
├── bot.c                 # 新增：BOT 状态机
├── bot.h                 # 新增
├── faults.c              # 重构：适配新架构
├── faults.h              # 修改：删除 F4，重定义 F9
├── expect.c              # 修改：删除 F4 expect，更新 F9
├── expect.h              # 不变
└── usb-msd-proto.h       # 不变
```

### 4.3 枚举流程 (Layer 1)

```
1. INIT(driver_name="20980000.usb", device_name="20980000.usb", speed=HIGH)
2. RUN
3. EVENT_FETCH 循环:
   CONNECT → 继续
   CONTROL → 解析 usb_ctrlrequest:
     GET_DESCRIPTOR(DEVICE)    → EP0_WRITE(18B device desc)
     GET_DESCRIPTOR(CONFIG)    → EP0_WRITE(32B config+iface+ep desc)
     GET_DESCRIPTOR(STRING,0)  → EP0_WRITE(4B LangID table)
     GET_DESCRIPTOR(STRING,N)  → EP0_WRITE(UTF-16LE string)
     SET_CONFIGURATION(1)      → EP0_WRITE(0B) + CONFIGURE ioctl
                                + EP_ENABLE(bulk-in) + EP_ENABLE(bulk-out)
                                + VBUS_DRAW(250)
                                + 保存 ep_in_handle / ep_out_handle
                                + 标记 enumerated=true
     GET_MAX_LUN(0xFE)         → EP0_WRITE(1B: 0x00)
     BULK_RESET(0xFF)          → EP0_WRITE(0B) + 重置 BOT 状态机
     其他                      → EP0_STALL
4. 进入 BOT 主循环
```

### 4.4 BOT 状态机 (Layer 2)

```
while (running) {
    [钩子A: stall-out → EP_SET_HALT(ep_out_handle)]
    EP_READ(ep_out_handle, cbw, 31) → 解析 CBW
    SCSI 分发 → 获得 data_buf, data_len, direction
    [钩子B: timeout → 收到 CBW 后不响应]
    if (direction == IN) {
        [钩子C: short → 少发 short_bytes]
        [钩子D: stall-in → EP_SET_HALT(ep_in_handle)]
        EP_WRITE(ep_in_handle, data_buf, data_len)
    }
    if (direction == OUT) {
        EP_READ(ep_out_handle, data_buf, data_len)
    }
    [钩子E: degrade → CSW 发送前 delay_ms]
    [钩子F: csw-sig/tag/status → 损坏 CSW 字段]
    [钩子G: abort → stall-in + 不响应直到 timeout]
    EP_WRITE(ep_in_handle, csw, 13)
}
```

### 4.5 SCSI 命令处理 (Layer 3)

| 命令 | OpCode | Data 方向 | 返回大小 | 内存盘行为 |
|------|--------|----------|----------|-----------|
| INQUIRY | 0x12 | IN | 36B | 固定标准响应 |
| TEST UNIT READY | 0x00 | 无 | 0B | — |
| REQUEST SENSE | 0x03 | IN | 18B | Sense Key=0 |
| READ CAPACITY(10) | 0x25 | IN | 8B | 131071 blocks × 512B = 64MB |
| MODE SENSE(6) | 0x1A | IN | 4B | 固定值 |
| PREVENT ALLOW | 0x1E | 无 | 0B | — |
| START STOP UNIT | 0x1B | 无 | 0B | — |
| READ(10) | 0x28 | IN | N×512B | 从内存盘读取 |
| WRITE(10) | 0x2A | OUT | — | 写入内存盘 |

内存盘：`calloc(1, 64*1024*1024)`，64MB 全零虚拟块设备。

### 4.6 USB 描述符

```
Device Descriptor:
  bcdUSB=0x0200, bDeviceClass=0(PER_INTERFACE)
  idVendor=0x1D6B(Linux Foundation), idProduct=0x0104(Multifunction Composite)
  bMaxPacketSize0=64, bNumConfigurations=1

Config Descriptor (wTotalLength=32):
  bNumInterfaces=1, bConfigurationValue=1
  bmAttributes=0x80(bus-powered), bMaxPower=0x32(100mA)

Interface Descriptor:
  bInterfaceClass=0x08(MASS_STORAGE)
  bInterfaceSubClass=0x06(SCSI transparent)
  bInterfaceProtocol=0x50(BULK-ONLY)
  bNumEndpoints=2

Endpoint IN:  bEndpointAddress=0x81, BULK, wMaxPacketSize=512
Endpoint OUT: bEndpointAddress=0x02, BULK, wMaxPacketSize=512

String Descriptors:
  0: LangID table [0x0409]
  1: "Lechao" (iManufacturer)
  2: "Pi02W Fault Inject" (iProduct)
  3: "FI0001" (iSerialNumber)
```

## 5. 关键实现细节

### 5.1 EP_ENABLE handle 获取

`USB_RAW_IOCTL_EP_ENABLE` 返回值是 UDC 端点数组索引（从 0 开始的整数），不是端点地址。必须先 `EPS_INFO` 查询可用端点，或直接传入描述符让内核匹配。

dwc2 (Pi Zero 2W) 的 bulk 端点：
- `EP_ENABLE` 传入 `{.bEndpointAddress=0x81, .bmAttributes=2, .wMaxPacketSize=512}` → 返回 `ep_in_handle`
- `EP_ENABLE` 传入 `{.bEndpointAddress=0x02, .bmAttributes=2, .wMaxPacketSize=512}` → 返回 `ep_out_handle`

### 5.2 EP0 控制传输处理

raw-gadget 每个 `USB_RAW_EVENT_CONTROL` 事件携带一个 `usb_ctrlrequest`（8B）。用户态必须通过以下方式之一响应：
- `EP0_WRITE`：IN 方向请求（如 GET_DESCRIPTOR）
- `EP0_READ`：OUT 方向请求有数据阶段时
- `EP0_STALL`：拒绝请求

**重要约束**：raw-gadget 不支持并发控制请求，同一时间只能有一个 pending。

### 5.3 Host 校验逻辑（影响故障注入效果）

来自 `drivers/usb/storage/transport.c::usb_stor_Bulk_transport()`:

| 校验项 | 失败条件 | Host 动作 |
|--------|---------|----------|
| CSW Signature | `!= 第一次学习的签名` | TRANSPORT_ERROR → reset recovery |
| CSW Tag | `!= CBW Tag` | TRANSPORT_ERROR → reset recovery |
| CSW Status | `> 2 (PHASE)` | TRANSPORT_ERROR → reset recovery |
| CSW Status | `== 2 (PHASE)` | TRANSPORT_ERROR → reset recovery (不走 auto-sense) |
| CSW Status | `== 1 (FAIL)` | TRANSPORT_FAILED → auto-sense (REQUEST SENSE) |
| Bulk STALL | 端点返回 -EPIPE | ClearHalt → 重试，连续 2 次失败 → reset |
| 传输超时 | SCSI midlayer 超时 (~30s) | abort → reset recovery |

### 5.4 VBUS_DRAW 注意事项

`USB_RAW_IOCTL_VBUS_DRAW` 参数单位是 **2mA**。`VBUS_DRAW(250)` = 500mA。

对于 hotplug/disconnect 故障，`VBUS_DRAW(0)` 拉低 VBUS 模拟断开。dwc2 debounce 约 200ms，单次离线 ≥ 1000ms 较稳定。

## 6. 不变项

- `usb-msd-proto.h`：协议常量定义不变
- `expect.h`：接口不变
- Host 侧 `usb-verify`、内核 `transport.c/usb.c` 补丁：不变

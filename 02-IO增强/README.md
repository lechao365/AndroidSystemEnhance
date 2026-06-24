# LcIod IO 监控系统

基于 notifier chain 的 USB 存储传输速率监控框架，覆盖内核态事件注入、驱动统计、用户态 HAL 代理、System 服务暴露全链路。配套外围故障注入与验证工具，构成 **注入 → 检测 → 上报 → 校验** 的完整闭环。

## 文档索引

| 编号 | 名称 | 层级 | 说明 |
|------|------|------|------|
| 02.01 | [内核态增强](./02.01-内核态增强-lciod-kernel.md) | 内核态 | usb-storage notifier 注入 + LcIod 驱动 |
| 02.02 | HAL 增强 | 用户态 (vendor) | IIoHal AIDL + 设备缓存 + ioctl 封装 |
| 02.03 | Daemon 增强 | 用户态 (system) | IIoService 代理 + 字段投影 + 监控线程 |
| 02.04 | [故障注入工具](./02.04-故障注入工具-usb-fault-inject.md) | 外围 (Device 端) | usb-fault-inject — 12 类协议级故障注入 CLI |
| 02.05 | [故障注入验证](./02.05-故障注入验证-usb-verify.md) | 外围 (Host 端) | usb-verify — 统计读取 + 事件等待 + 阈值断言 CLI |

> **阅读建议**：先读本 README 建立全局视角，再按层级深入子文档。02.01-02.03 为监控系统主体，02.04-02.05 为外围验证工具。

## 逻辑视图

### 组件分解

三层架构，双层 AIDL 代理，每层单一职责：

```plantuml
@startuml
skinparam packageStyle rectangle
skinparam componentStyle rectangle

package "内核空间" {
    component "usb-storage 核心\n(原生)" as USBSTOR {
        component "transport.c\n注入点：START/END/ERROR\nSTALL/TIMEOUT/CORRUPT" as TRANS
        component "usb.c\n厂商 notifier：PROBE/DISCONNECT" as USBC
    }
    component "LcIod 驱动\n(built-in)" as LCIOD {
        component "vendor_notifier\nblocking chain" as VNOT
        component "per-device notifier\natomic chain" as PNOT
        component "stats engine\nrate + degrade" as STATS
        component "event_buf\nwait_queue" as EVBUF
        component "/dev/vendor_lechao_usbdN" as DEV
    }
    component "LcView 框架\nEXPORT_SYMBOL" as LCVIEW
}

package "vendor 域" {
    component "lechao_lciod_hal" as HAL {
        component "DeviceMap\nminor → {path, fd}" as DMAP
        component "ioctl 封装\nget_stats/reset/config" as IOCTL
        component "AIDL Server\nIIoHal [Pull]" as AIDL_V
    }
}

package "system 域" {
    component "lechao_lciod (Daemon)" as DAEMON {
        component "IoHalClient\nBinder 客户端" as HALC
        component "Field Projection\nvendor → system" as PROJ
        component "AIDL Server\nIIoService [Pull]" as AIDL_S
        component "monitor_thread\ndetach 后台监控" as MONITOR
    }
}

USBSTOR --> LCIOD : **blocking vendor_notifier\natomic per-device notifier**
LCIOD --> LCVIEW : **lcview_builder_start/commit**
LCIOD --> DEV : **cdev_add + device_create**

DEV --> HAL : **open/read/poll/ioctl**
HAL --> DAEMON : **vndbinder IIoHal/default**
DAEMON --> AIDL_S : **注册 system 服务**
MONITOR --> HALC : **周期轮询事件/统计**
@enduml
```

### notifier chain 类型

双层 notifier 链分工明确：

| notifier 链 | 类型 | 事件 | 调用上下文 | 注册位置 |
|-------------|------|------|-----------|---------|
| `usb_stor_vendor_nh` | blocking | PROBE/DISCONNECT | 进程上下文（可睡眠） | usb.c |
| `us->notifier` | atomic | START/END/ERROR/STALL/TIMEOUT/CORRUPT/RESET | 任意上下文（不可睡眠） | transport.c |

**与 LcView 的复用关系**：LcIod 调用 LcView `EXPORT_SYMBOL` 打点 API，将所有 PROBE/DISCONNECT/传输事件同步上送时序分析。LcIod Kconfig `select LCVIEW` 自动启用依赖。

### 跨层契约

ioctl ABI（`vendor_lechao_usbd-ioctl.h`）是三层共享契约：

```plantuml
@startuml
rectangle "ioctl ABI（三层共享契约）\n" as ABI {
    rectangle "struct vendor_lechao_usbd_stats\n23 字段：累计计数器 + 快照 + 设备标识 + 配置状态" as STATS
    rectangle "struct vendor_lechao_usbd_config\nenabled + flags" as CONFIG
    rectangle "struct vendor_lechao_usbd_event\ntimestamp + type + value + status + direction" as EVENT
}

note bottom of ABI
  内核 → HAL → Daemon 三层共享
  HAL: raw struct → IoStats parcelable
  Daemon: vendor IoStats → system IoStats（字段投影）
end note
@enduml
```

ioctl 命令码（魔数 `'R'`）：`GET_STATS`、`RESET_STATE`、`GET_CONFIG`、`SET_CONFIG`。

### 设计原则

| 原则 | 说明 |
|------|------|
| notifier chain 注入 | 零侵入 usb-storage 核心逻辑，仅添加事件发射点 |
| per-device 多实例 | 每个 USB 存储设备独立监控实例（最多 16 个），IDA 分配次设备号 |
| 双层 AIDL 代理 | system daemon 抽象 vendor HAL，字段投影屏蔽底层细节 |
| 端到端实时监控 | 内核事件 → notifier → HAL → Daemon → logcat，毫秒级延迟 |

## 过程视图

### 端到端数据流

```plantuml
@startuml
skinparam maxMessageSize 120

participant "usb-storage\ntransport()" as TRANS
participant "per-device\natomic_notifier" as PNOT
participant "LcIod\nstats engine" as STATS
participant "event_buf\nwait_queue" as EVBUF
participant "HAL\nrefresh_devices" as HAL
participant "HAL\nioctl/read" as IOCTL
participant "Daemon\nmonitor_thread" as MONITOR
participant "logcat" as LOG

TRANS -> PNOT : notifier_call(START, nd)
PNOT -> STATS : handle_event(START)
STATS -> STATS : transport_start_time = ktime_get()

TRANS -> TRANS : 执行传输

alt 传输成功
    TRANS -> PNOT : notifier_call(END, nd)
    PNOT -> STATS : handle_event(END)
    STATS -> STATS : 累计字节 + 计算速率 + degrade 检测
    STATS -> EVBUF : event_push（如有异常）
else 传输出错
    TRANS -> PNOT : notifier_call(ERROR + END)
    STATS -> EVBUF : event_push(TRANSPORT_ERROR)
end

EVBUF -> IOCTL : wake_up_interruptible → poll 就绪

HAL -> HAL : refresh_devices()（每次 AIDL 调用前）
IOCTL -> STATS : ioctl GET_STATS
STATS --> IOCTL : struct vendor_lechao_usbd_stats
IOCTL -> EVBUF : read（阻塞等待事件）
EVBUF --> IOCTL : struct vendor_lechao_usbd_event

IOCTL -> MONITOR : vndbinder readEvent/getStats
MONITOR -> LOG : ALOGI("monitor: read_rate=... KB/s")
@enduml
```

### 关键指标

| 指标 | 设计值 | 保障机制 |
|------|--------|---------|
| 事件延迟 | 毫秒级 | atomic notifier chain + wait_queue |
| 多设备支持 | 16 个 | IDA 次设备号分配 + kref 引用计数 |
| 热插拔感知 | 实时 | blocking vendor notifier + HAL refresh_devices |
| 端到端监控 | 实时 logcat | Daemon detach 后台线程（50ms 轮询） |
| degrade 检测 | 1 秒滑动窗口 | 速率下降 2x 或延迟上升触发 |

## 开发视图

### 源码组织

三条构建线，共享 ioctl ABI 头文件：

```plantuml
@startuml
package "内核构建线" {
    rectangle "patchs/rpi5/kernel/new/\nvendor/lechao/LcIod/\n---\nlciod_usbd.c (字符设备)\nlciod_usbd-stats.c (统计引擎)\nlciod_usbd.h (per-device)\nlciod_usbd-ioctl.h (ABI)\nKconfig / Makefile" as KSRC
    rectangle "patchs/rpi5/kernel/modified/\ndrivers/usb/storage/\n---\nusb.h.diff (notifier 枚举)\nusb.c.diff (vendor notifier)\ntransport.c.diff (注入点)" as MODSRC
}

package "HAL 构建线" {
    rectangle "patchs/rpi5/aosp/new/\nvendor/lechao/services/lechao_lciod/hal/\n---\nhal_service.cpp\ndevice_io.cpp\nlechao_lciod_hal.rc\nvendor.lechao.lciod.IIoHal-service.xml" as HALSRC
}

package "Daemon 构建线" {
    rectangle "patchs/rpi5/aosp/new/\nvendor/lechao/services/lechao_lciod/daemon/\n---\nservice.cpp\nhal_client.cpp\nlechao_lciod.rc" as DAEMONSRC
}

rectangle "lciod_usbd-ioctl.h\n(跨层共享契约)\nstats/config/event + ioctl 命令码" as SHARED

SHARED ..> KSRC
SHARED ..> HALSRC
KSRC --> MODSRC : **注入 notifier chain**
@enduml
```

### 构建集成

| 构建线 | 构建系统 | 产物 |
|--------|---------|------|
| 内核驱动 | Kconfig + Makefile (`CONFIG_VENDOR_LECHAO_USBD=y`) | built-in 到 vmlinux |
| HAL 进程 | Soong `cc_binary` (`vendor: true`) | `/vendor/bin/lechao_lciod_hal` |
| Daemon 进程 | Soong `cc_binary` | `/system/bin/lechao_lciod` |
| vendor AIDL | Soong `aidl_interface` (`@VintfStability`) | VINTF 稳定的 NDK 后端 |
| system AIDL | Soong `aidl_interface` (`unstable: true`) | 开发阶段 NDK 后端 |

## 部署视图

### 运行时拓扑

```plantuml
@startuml
skinparam nodeStyle rectangle

node "内核空间" {
    component "LcIod 驱动\n(device_initcall)\n/dev/vendor_lechao_usbd0~15" as KDRV
}

node "vendor 分区" {
    component "lechao_lciod_hal\n(class hal, oneshot)" as HALP
    component "VINTF Manifest\nIIoHal-service.xml" as VINTF
}

node "system 分区" {
    component "lechao_lciod\n(class main, oneshot)\nboot_completed 触发" as DAEMONP
}

KDRV --> HALP : char device\nopen/read/poll/ioctl
HALP --> DAEMONP : vndbinder\nIIoHal/default
VINTF --> HALP : 服务声明
@enduml
```

### 安全域隔离

四个 SELinux 域，两次跨域通信：

| 域 | 运行身份 | 核心权限 | 不需要的权限 |
|---|---|---|---|
| 内核空间 | kernel | — | — |
| `lechao_lciod_hal` (vendor) | `system:system` | 读字符设备 + 注册 AIDL + 写 logd | 不写文件、不访问网络 |
| `lechao_lciod` (system) | `system:system` | vndbinder 调用 + 注册服务 + 写 logd | 不读字符设备 |

**跨域通信**：
1. **内核 → HAL**：字符设备 `/dev/vendor_lechao_usbd*`（`lechao_lciod_hal_device` 类型）
2. **HAL → Daemon**：vndbinder AIDL `vendor.lechao.lciod.IIoHal/default`（`lechao_lciod_hal_service` 类型）

### 启动顺序

```plantuml
@startuml
participant "init" as I
participant "内核\n(device_initcall)" as K
participant "HAL\n(vendor 域)" as H
participant "Daemon\n(system 域)" as D

== boot 阶段 ==
I -> K : vmlinux 启动
K -> K : device_initcall\n注册 vendor notifier\n扫描已有设备

I -> H : class hal 启动
activate H
H -> H : refresh_devices\n构建 DeviceMap
H -> K : open / ioctl GET_STATS
H -> H : 注册 IIoHal/default

== boot_completed 阶段 ==
I -> I : sys.boot_completed=1
I -> D : start lechao_lciod
activate D
D -> H : checkService(IIoHal/default)
H --> D : AIBinder*
D -> D : 启动 monitor_thread（detach）
D -> H : readEvent + getStats（周期轮询）

deactivate D
deactivate H
@enduml
```

## 跨层设计决策

| 决策 | 架构动机 | 详细展开 |
|------|---------|---------|
| **notifier chain 注入** | 零侵入核心代码，升级无 merge conflict | [02.01](./02.01-内核态增强-lciod-kernel.md) § usb-storage notifier chain |
| **per-device 多实例** | 每个 USB 设备独立监控，避免统计混淆 | [02.01](./02.01-内核态增强-lciod-kernel.md) § IDA 分配 + kref |
| **双层 AIDL 代理** | system daemon 抽象 vendor HAL，屏蔽 ioctl 细节 | daemon/service.cpp § IoServiceImpl |
| **字段投影** | 管理字段（enabled/flags）不暴露给上层 | daemon/service.cpp § getIoStats |
| **detach 监控线程** | 后台轮询事件/统计，实时 logcat 输出 | daemon/service.cpp § start_monitor |

## 与 LcView 的关系

LcIod 内核驱动 `select LCVIEW`（Kconfig），调用 LcView `EXPORT_SYMBOL` API 进行结构化打点：

| LcView 事件 ID | 字段 | 级别 | 触发时机 |
|---------------|------|------|---------|
| `LCVIEW_EVENT_USB_PROBE` | device_index, vid, pid, vendor, product | INFO | 设备插入 |
| `LCVIEW_EVENT_USB_DISCONNECT` | device_index | INFO | 设备拔出 |
| `LCVIEW_EVENT_USB_TRANSPORT_START` | device_index, direction, data_len | DEBUG | 传输开始 |
| `LCVIEW_EVENT_USB_TRANSPORT_END` | device_index, direction, bytes, elapsed_ns, was_error | INFO | 传输结束 |
| `LCVIEW_EVENT_USB_TRANSPORT_ERROR` | device_index, direction, result | WARN | 传输出错 |
| `LCVIEW_EVENT_USB_STALL/TIMEOUT/CORRUPT/RESET` | device_index, status | WARN | 异常事件 |
| `LCVIEW_EVENT_USB_RATE_DEGRADED` | device_index, latency_ns | WARN | 性能降级 |

LcView 提供 9 个 USB 事件 ID，LcIod 在 notifier 回调中调用 `lcview_builder_start/commit` 上送。

## 与 LcView 的架构对比

| 维度 | LcView (01) | LcIod (02) |
|------|-------------|------------|
| 设备模型 | 单设备 | 多设备（最多 16 个） |
| 内核侵入 | 零侵入（纯 EXPORT_SYMBOL） | 需修改 usb-storage 核心（notifier 注入） |
| AIDL 层级 | 单层 vendor ILcView | 双层（vendor IIoHal + system IIoService） |
| System daemon 角色 | 消费者（校验+落盘） | 代理（转发+投影+监控） |
| 数据传输 | 批量二进制流 read（64KB） | 定长 struct read + ioctl 轮询 |
| 监控对象 | 通用事件打点 | USB 存储传输速率/错误/降级 |
| 事件推送 | HAL 批量累积 flush | 内核 per-device wait_queue 阻塞读 |

## 外围验证组件（故障注入闭环）

02.04-02.05 构成独立于监控主体的外围验证工具链，用于端到端校验 LcIod 监控系统的故障检测能力。

### 闭环架构

```plantuml
@startuml
skinparam packageStyle rectangle

package "Pi Zero 2W (USB Device)" {
    component "usb-fault-inject\n02.04" as INJECT {
        component "Raw Gadget 驱动层\nraw-gadget.c (543行)" as RG
        component "11 类故障实现\nfaults.c (145行)" as FAULTS
        component "期望值契约表\nexpect.c (114行)" as EXPECT
    }
}

package "Pi 5 (USB Host)" {
    component "LcIod 内核驱动\n02.01" as LCIOD {
        component "notifier chain\n事件捕获" as NOTIFIER
        component "stats engine\n统计引擎" as STATS2
    }
    component "usb-verify\n02.05" as VERIFY {
        component "ioctl + poll\n设备操作层" as DEV
        component "阈值断言引擎\ncheck 层" as CHECK
        component "文本/JSON 输出\noutput 层" as OUT
    }
}

INJECT --> RG : ioctl /dev/raw-gadget
RG --> NOTIFIER : USB 协议层故障\nSTALL/TIMEOUT/CORRUPT/...
NOTIFIER --> STATS2 : 捕获 + 累计统计
STATS2 --> VERIFY : /dev/vendor_lechao_usbdN\nioctl GET_STATS + read event
EXPECT --> VERIFY : stdout JSON\nexpect_table 契约
CHECK --> OUT : PASS / FAIL\n退出码 0/5
@enduml
```

### 12 类故障映射

| ID | 故障 | Device 命令 | 内核捕获事件 | 校验命令 |
|----|------|------------|-------------|---------|
| F1/F2 | STALL | `stall-in` / `stall-out` | STALL + ERROR + RESET | `check stats --stall-ge 1` |
| F3 | Timeout | `timeout --duration 5000` | TIMEOUT + ERROR + RESET | `check stats --timeout-ge 1` |
| F4-F7 | Corrupt | `corrupt --field cbw-sig/csw-sig/csw-tag/csw-status` | CORRUPT + ERROR + RESET | `check stats --corrupt-ge 1` |
| F8 | Short | `short --bytes 512` | CORRUPT + ERROR | `check stats --corrupt-ge 1` |
| F9 | Abort | `abort --ep in/out` | ERROR + RESET | `check stats --error-ge 1` |
| F10 | Hotplug | `hotplug --cycles 3` | DISCONNECT + PROBE | 设备节点观察 |
| F11 | Disconnect | `disconnect` | DISCONNECT | 设备节点观察 |
| F12 | Degrade | `degrade --delay 50` | RATE_DEGRADED | `check degrade --rate-drop-ge N` |

### 端到端工作流

```
1. usb-verify stats reset --device /dev/vendor_lechao_usbd0   # Host 清零统计
2. usb-fault-inject stall-out                              # Device 注入故障
   → stdout: {"fault":"stall","expect":{"error_count":1,...}}
3. usb-verify check stats --device /dev/vendor_lechao_usbd0 \  # Host 校验
     --stall-ge 1 --error-ge 1 --reset-ge 1
   → PASS (退出码 0) / FAIL (退出码 5)
```

## 相关资源

- **内核源码**：[`patchs/rpi5/kernel/new/vendor/lechao/LcIod/`](../patchs/rpi5/kernel/new/vendor/lechao/LcIod/) — LcIod 驱动
- **usb-storage 修改**：[`patchs/rpi5/kernel/modified/drivers/usb/storage/`](../patchs/rpi5/kernel/modified/drivers/usb/storage/) — notifier 注入点
- **用户态源码**：[`patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lciod/`](../patchs/rpi5/aosp/new/vendor/lechao/services/lechao_lciod/) — HAL + Daemon + AIDL
- **SELinux 策略**：[`patchs/rpi5/aosp/new/device/brcm/rpi5/sepolicy/`](../patchs/rpi5/aosp/new/device/brcm/rpi5/sepolicy/) — `lechao_lciod.te` + `lechao_lciod_hal.te`
- **故障注入工具**：[`patchs/rpi-zero2w/others/usb-fault-inject/`](../patchs/rpi-zero2w/others/usb-fault-inject/) — Device 端 12 类故障注入
- **故障校验工具**：[`patchs/rpi5/others/usb-verify/`](../patchs/rpi5/others/usb-verify/) — Host 端统计校验 CLI
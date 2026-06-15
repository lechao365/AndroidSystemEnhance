# LcIod 文档编写设计规格

## 目标

在 `02-IO增强/` 目录下新增 4 个文档，严格遵循 `templates/module-template.md` 和 `module-readme-template.md` 格式，内容基于 `patchs/rpi5/` 中的 LcIod 实际代码。

## 文件清单

```
02-IO增强/
├── README.md                          # 模块总览（module-readme-template）
├── 02.01-内核态增强-lciod-kernel.md    # 内核驱动（module-template）
├── 02.02-HAL增强-lciod-hal.md          # Vendor HAL（module-template）
└── 02.03-Daemon增强-lciod-daemon.md    # System Daemon（module-template）
```

## 文档边界划分

### 02.01 内核态增强

**范围**：usb-storage notifier chain 注入（子章节） + vendor/lechao/LcIod/ 驱动主体（主章节）

**usb-storage 子章节**：
- 修改文件：`drivers/usb/storage/transport.c`、`usb.c`、`usb.h`
- 内容：notifier chain 定义、注入点（7 类事件）、事件 payload 结构体
- 定位：与 LcIod 驱动同级，未来可扩展 block/scsi 等其他注入源

**LcIod 驱动主体**：
- 6 个文件：`lciod_usbd.c`（932行）、`lciod_usbd-stats.c`（665行）、`lciod_usbd.h`（142行）、`lciod_usbd-ioctl.h`（166行）、`Kconfig`、`Makefile`
- 核心数据结构：`vendor_lechao_usbd_device`（per-device）、`vendor_lechao_usbd_stats`、`vendor_lechao_usbd_event`、`vendor_lechao_usbd_config`
- 关键设计：
  1. Notifier chain 回调 → 统计更新 + degrade 检测 + 事件推送 + LcView 打点
  2. Per-device 多实例（最多 16 个），IDA 分配次设备号
  3. 三重锁层次：全局 mutex（设备链表） → per-device lock（stats） → per-device event_lock（事件缓冲区）
  4. kref 引用计数保护 open/release 竞态
  5. 事件环形缓冲区（32 条）+ wait_queue 阻塞读 + poll 支持
  6. Degrade 滑动窗口检测（1s 窗口，2x 阈值）
  7. Atomic notifier 上下文安全（spinlock + 无睡眠）
  8. LcView 结构化打点集成（9 种事件类型）
  9. ioctl ABI：GET_STATS / RESET_STATE / GET_CONFIG / SET_CONFIG

### 02.02 HAL 增强（仅 vendor IIoHal）

**范围**：`hal/` 目录下全部文件

**文件**：
- `hal_service.cpp`（352行）：IoHalImpl 类，IIoHal AIDL 实现
- `device_io.cpp`（162行）+ `device_io.h`（85行）：C 风格设备 IO 封装
- `vendor_lechao_usbd-ioctl.h`：内核/用户态共享 ABI 头文件
- `lechao_lciod_hal.rc`：init 启动脚本
- `vendor.lechao.lciod.IIoHal-service.xml`：VINTF manifest
- `Android.bp`：Soong 编译配置
- AIDL 接口：`IIoHal.aidl`、`IoStats.aidl`、`IoConfig.aidl`、`IoEvent.aidl`

**关键设计**：
1. DeviceEntry 缓存 + refresh_devices 热插拔（glob 枚举）
2. 持久 fd（readEvent）vs 临时 fd（getStats 等）策略
3. read_event 排空策略（poll + 循环 read，保留最新）
4. 单 Binder 线程（无并发风险）
5. 字段映射：内核 struct → AIDL parcelable

### 02.03 Daemon 增强（system IIoService）

**范围**：`daemon/` 目录下全部文件

**文件**：
- `service.cpp`（305行）：IoServiceImpl 类，IIoService AIDL 实现 + 后台监控线程
- `hal_client.cpp`（89行）+ `hal_client.h`（38行）：HAL 客户端连接管理
- `lechao_lciod.rc`：init 启动脚本
- `Android.bp`：Soong 编译配置
- AIDL 接口：`IIoService.aidl`、`IoStats.aidl`、`IoConfig.aidl`、`IoEvent.aidl`

**关键设计**：
1. 代理转发模式：system AIDL → vendor HAL（路径→minor 转换）
2. 字段投影：vendor IoStats → system IoStats（省略 currentRate/enabled/flags/peakRate）
3. 计算字段：getAverageRate（总字节/总耗时）
4. IoHalClient 延迟连接 + 指数退避重连 + 死亡通知
5. detach 后台监控线程（50ms 事件轮询 + 10s 统计快照）

### README.md

**结构**（module-readme-template）：
- 文档索引：02.01 内核 / 02.02 HAL(vendor) / 02.03 Daemon(system)
- 逻辑视图：三层组件分解图
- 跨层契约：ioctl ABI（`vendor_lechao_usbd-ioctl.h`）是三层共享契约
- 过程视图：端到端数据流时序图（usb-storage → notifier → 驱动 → HAL → Daemon）
- 开发视图：三条构建线
- 部署视图：四个 SELinux 域 + 设备节点权限
- 跨层设计决策：notifier chain 注入、per-device 多实例、双层 AIDL 代理、字段投影

## 与 01-打点增强 的关键差异

| 维度 | LcView (01) | LcIod (02) |
|------|-------------|------------|
| 设备模型 | 单设备 `/dev/vendor_lechao_lcview` | 多设备 `/dev/vendor_lechao_usbd0~15` |
| 内核侵入 | 零侵入（纯 EXPORT_SYMBOL） | 需修改 usb-storage 核心（notifier chain） |
| AIDL 层级 | 单层 vendor ILcView | 双层（vendor IIoHal + system IIoService） |
| System daemon 角色 | 消费者（校验+落盘） | 代理（转发+投影+监控） |
| 数据传输 | 批量二进制流 read | 定长 struct read + ioctl 轮询 |
| HAL 数据模型 | 批量搬运（不理解格式） | 字段映射（struct→parcelable） |
| 监控对象 | 通用事件打点 | USB 存储传输速率/错误/降级 |

## PlantUML 约束

遵循 `rules/plantuml.md`：
- 禁止空图块
- UML 块内禁止花括号占位符
- 条件块内禁止 fork

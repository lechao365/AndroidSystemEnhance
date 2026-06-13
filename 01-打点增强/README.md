# LcView 日志打点系统

基于 Schema ID 的结构化日志打点框架，覆盖内核态 Builder API 打点、环形缓冲区传输、用户态 HAL 批量采集、Daemon 校验落盘全链路。

## 文档索引

| 编号 | 名称 | 层级 | 说明 |
|------|------|------|------|
| 01.01 | [内核态增强-lcview-kernel](./01.01-内核态增强-lcview-kernel.md) | 内核态 | Builder API 打点 + 环形缓冲区 + 字符设备 `/dev/vendor_lechao_lcview` + ioctl 控制 |
| 01.02 | [HAL增强-lcview-hal](./01.02-HAL增强-lcview-hal.md) | 用户态(vendor) | HAL：epoll 批量读取 + ILcView AIDL Pull 接口 + 阻塞式 getBatch + 双缓冲队列 |
| 01.03 | [Daemon增强-lcview-daemon](./01.03-Daemon增强-lcview-daemon.md) | 用户态(system) | Daemon：Schema 校验 + JSONL 分文件落盘 + 文件滚动 + 保留策略 |

## 架构概览

```
┌──────────────────────────────────────┐
│          其他内核模块（调用方）          │
│    lcview_builder_start/add/commit    │
└──────────────┬───────────────────────┘
               │ EXPORT_SYMBOL
┌──────────────▼───────────────────────┐
│         LcView 内核模块（built-in）       │
│  Builder → Ring Buffer → Char Dev    │
└──────────────┬───────────────────────┘
               │ /dev/vendor_lechao_lcview
┌──────────────▼───────────────────────┐
│     lechao_lcview_hal                  │  vendor 域
│     HAL: epoll + 64KB批量读取         │
└──────────────┬───────────────────────┘
               │ Binder AIDL getBatch() [Pull]
┌──────────────▼───────────────────────┐
│         lechao_lcview                  │  system 域
│  Schema校验 → JSONL落盘 → 文件滚动    │
└──────────────┬───────────────────────┘
               │
               ▼
    /data/vendor/lechao_lcview/logs/
    {id}_{name}_{date}_p{seq}.jsonl
```

**设计原则：**
1. **Schema ID 驱动**：每个事件有固定 ID 和预定义字段 schema，传输只传 ID + 值，字段名由 JSON 定义
2. **Pull 模式健壮**：daemon 通过 `getBatch()` 无状态拉取，无回调生命周期、无死亡监听
3. **按 ID 分文件**：不同 event_id 写入独立 JSONL 文件，云端一对一建表
4. **批量高效**：HAL 64KB 累积 + 1s 超时，减少 syscall 和 IPC
5. **安全校验**：daemon 逐字段校验类型和数量，不合法记录拒绝落盘

## 架构分析与代码讲解

### 3.1 架构核心优势

#### 3.1.1 Schema 模板 + 数据解耦

```
┌───────────────────────┐      ┌──────────────────────────┐
│   JSON Schema（模板）    │      │   二进制记录（数据）        │
│  /vendor/etc/lcview    │      │   环形缓冲区中传输          │
│  _events.json          │      │                           │
│  ┌───────────────────┐ │      │  ┌──────────────────────┐ │
│  │ event_id: 4        │ │      │  │ magic(2B)            │ │
│  │ name: transport_   │ │      │  │ event_id(2B) = 4     │ │
│  │ start              │ │      │  │ timestamp_ns(8B)     │ │
│  │ fields:            │ │      │  │ field_count(1B) = 3  │ │
│  │  - device_index    │ │      │  │ [int64]device_index  │ │
│  │  - data_direction  │ │ 匹配 │  │ [int64]data_direction│ │
│  │  - bytes_to_xfer   │ │◄────►│  │ [int64]bytes_to_xfer │ │
│  └───────────────────┘ │      │  └──────────────────────┘ │
│       (字段名+类型)      │      │    (仅传 ID + 值，无字段名)  │
└───────────────────────┘      └──────────────────────────┘
```

**设计优势：**

| 维度 | 传统 printf/klog 方案 | LcView 方案（本架构） |
|------|---------------------|-------------------|
| 存储效率 | 每条日志包含重复的字段名和格式字符串 | 二进制格式，字段名仅存一份在 schema 中，传输只传值 |
| 解析效率 | 正则匹配解析，CPU 密集 | 逐字段按 schema 偏移量解析，O(n) |
| 扩展性 | 新增字段需改所有消费端代码 | 新增字段仅改 schema，历史数据兼容 |
| 数据完整性 | 字符串拼接可能被截断或格式错误 | 二进制序列化，类型安全 |
| 传输效率 | 字符串在 total_bytes 中占比高 | 二进制紧凑格式，带宽友好 |

**二进制记录格式详解：**

```
┌─ 长度前缀（4B）────┬─ 记录头（16B）────────┬─ 字段值区 ───────────┐
│ total_len(uint32)  │ magic=0x4C56        │ type(1B) + value    │
│ (含自身 + 记录体)    │ event_id            │ INT64:  8B          │
│                    │ level                │ INT32:  4B          │
│                    │ field_count          │ STRING: 2B(len)+data│
│                    │ timestamp_ns         │ FLOAT:  4B          │
│                    │ reserved             │ BINARY: 2B(len)+data│
└────────────────────┴──────────────────────┴──────────────────────┘
```

#### 3.1.2 数据 + 字段名分离优势

**传统方式（内核 printk）：**
```c
printk("usb_transport_start: dev=%d dir=%d bytes=%lld\n",
       device_index, dir, bytes);
```
- 每次生成 50+ 字节字符串
- 用户态需正则解析
- 字段名和格式字符串每次都能在日志中重复

**LcView 方式：**
```c
b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_START, LEVEL_INFO);
lcview_builder_add_int(b, device_index); // 仅传值，不传名
lcview_builder_add_int(b, dir);
lcview_builder_add_int(b, bytes);
lcview_builder_commit(b);
```
- 每条记录仅传 16B 头 + 3×9B 字段 = 43B（vs 70B+ 字符串）
- 字段名存在于 JSON schema 中，由用户态 Daemon 关联
- 字段值以二进制形式传输，无需格式化

#### 3.1.3 Pull 模式 vs Push 模式

```
Push 模式（回调）                  Pull 模式（本方案）
  HAL                               HAL
  │                                 │
  ├── onEvent(data) ──→ daemon      │  daemon 主动拉
  │   (回调注册需管理生命周期)        │
  ├── onEvent(data) ──→ daemon      ├── getBatch() ──→ HAL
  │   (daemon 崩溃需重新注册)        │←── byte[] ────┘
  └── daemon 崩溃 ──→ 回调丢失       │  (无状态拉取)
```

**Pull 模式优势：**
- **无状态**：daemon 崩溃重启后直接调 `getBatch()` 即可继续消费，无需重新注册回调
- **自然背压**：daemon 处理慢时数据在 HAL 内部排队，不会压垮 daemon
- **无死亡监听**：不需要 `linkToDeath` + 重连逻辑
- **批量传输**：一次 Binder IPC 可传输 64KB 批量数据，Push 模式单条传输开销大

**v2.3 优化 (H3): 阻塞式 Pull 消除轮询开销**

getBatch() 原先立即返回空 batch[] 时，daemon 需 sleep 100ms 后重试（高频场景约 10 次无效 Binder 调用/s）。v2.3 改为阻塞式：HAL 内部维护 `std::condition_variable`，数据到达时 readerLoop 通过 `notify_one()` 唤醒等待中的 getBatch()。daemon 紧循环调用即可，无额外 sleep 开销。最多等待 1s（与 epoll 超时对齐）后返回空批次。

#### 3.1.4 HAL 批量读取策略

```
readerLoop (后台线程):
    # 启动时查询 ring 初始统计
    ioctl(LCVIEW_GET_STATS) → log total_records/usage/overrun

    while (running):
        epoll_wait(fd, 1s)
          ├── 就绪 → read(fd, hal_buf + offset, remaining)
          │            offset += n
          │            if offset == 0: dataArrivedAt = now()
          └── 超时 → (继续检查 flush 条件)

        # 每 30 次输出心跳 + 诊断计数器
        if ++beat % 30 == 0:
            LOG(INFO) "alive readOk=X readErr=X flush=X"

        # flush 条件：有数据 && (满 64KB || epoll 超时 || 首字节滞留 > 500ms)
        if offset > 0 && (offset >= 64KB || nfds==0 || age > 500ms):
            mBatchQueue.push_back(hal_buf)
            mBatchCv.notify_one()
            offset = 0

        # 每 30 次查询溢出计数
        if beat % 30 == 0:
            ioctl(GET_OVERRUN) → 累加 overrun

    # 退出时打印最终统计
    LOG(INFO) "readerLoop exiting readOk=X flush=X beat=X"
```

**为什么是 64KB 批量 + 1s 超时？**

| 参数 | 设计考量 |
|------|---------|
| 64KB 批量 | 匹配 Binder 传输适宜大小，减少 IPC 频率 |
| 1s 超时 | 低频场景数据延迟不超过 1s，高频场景按满 64KB 触发 |
| 500ms age | v2.4: 首字节到达 500ms 内强制投递，解决低频事件数据滞留 |
| epoll | 相比纯 read 阻塞，支持超时 + 多 fd 扩展 |
| 锁外 copy_to_user | 锁内 memcpy 到 read_buf 后解锁，避免锁内系统调用 |
| 诊断计数器 | v2.4: readOk/readErr/flushCount，心跳输出完整数据流信息 |
| v3.4 队列深度 | 4 批次 | 双缓冲队列 `std::deque<std::vector<uint8_t>>` 最多缓存 4 个批次，超限丢弃最旧 |

#### 3.1.5 文件滚动设计

```
按时间（每天）       按大小（50MB）      保留策略（500MB）
    │                    │                    │
    ▼                    ▼                    ▼
1_usb_connect_20260603_p0.jsonl    ← 当前写入文件
                      │ 超 50MB
                      ▼
1_usb_connect_20260603_p1.jsonl    ← 滚动后新文件
                      │ 第二天
                      ▼
1_usb_connect_20260604_p0.jsonl    ← 新日期新文件
```

**为什么用 JSONL 而非 JSON 数组？**
- **追加写**：JSONL 每次写一行，O(1) 尾部追加；JSON 数组需改写整个文件或在内存中反序列化后追加再写回
- **流式解析**：每行可独立解析，即使文件中途损坏也不影响前后行
- **grep 友好**：可直接用 shell 工具 grep/awk 查询

### 3.2 SchemaParser 校验详解

#### 3.2.1 校验流程

```
Daemon 主循环：
  batch = hal->getBatch()
  offset = 0
  while offset < batch.size():
    total_len = parse_u32(batch[offset])
    if total_len < 4 || offset + total_len > batch.size(): break

    record = batch[offset+4 .. offset+total_len-4]

    # 校验魔法数
    if record.magic != 0x4C56 → invalid

    # 查找 Schema
    schema = schemaParser.find(record.event_id)
    if schema == nullptr → invalid

    # 逐字段校验
    for each field in record:
      check wireType == schema.fieldType
      check field length bounds
      check total consumed == recordLen

    # 校验通过 → 写入文件
    writer.writeRecord(schema, record)
    offset += total_len
```

#### 3.2.2 为什么需要独立校验层？

| 考虑 | 说明 |
|------|------|
| 数据完整性 | 内核中可能产生损坏数据（内存错误、驱动 bug），需在用户态防御 |
| 版本兼容 | 内核模块和 JSON schema 可能版本不同步，校验可检测不匹配 |
| 安全 | 恶意损坏数据可能通过环形缓冲区注入，校验防止注入攻击 |
| 诊断 | 无效记录写入独立文件，保留原因便于调试 |

### 3.3 SELinux 策略对比

#### 3.3.1 lechao_lcview vs lechao_lciod 策略差异

| 策略项 | lechao_lciod (IOD) | lechao_lcview (LCVIEW) |
|--------|-----------------|----------------------|
| 设备节点类型 | `lechao_lciod_hal_device` | `lechao_lcview_hal_device` |
| 数据文件类型 | 无（仅读取设备节点） | `lechao_lcview_data_file`（写入日志文件） |
| 数据目录权限 | 无 | `allow daemon data_file:dir { create_dir_perms }` |
| 数据文件权限 | 无 | `allow daemon data_file:file { create_file_perms }` |
| 服务管理 | `add lciod_service` | 仅 `find` HAL 服务 |

**为什么 lcview 需要 data_file 权限而 lciod 不需要？**

- lcview daemon 需要**写入**日志文件到 `/data/vendor/lechao_lcview/logs/`，因此需要 `create_file_perms`（创建文件、写入、追加等）
- lciod daemon 仅**读取**内核统计数据，不写入持久化文件，因此不需要 data_file 类型
- iod 需要注册自己的 system 服务（`lciod_service`），因此有 `service_manager add`；lcview 不在 system 域注册服务，仅作为消费者拉取 HAL 数据

#### 3.3.2 `service_contexts` 的 `/default` 后缀

```
# 必须同时包含两条：
vendor.lechao.lcview.ILcView              u:object_r:lechao_lcview_hal_service:s0
vendor.lechao.lcview.ILcView/default      u:object_r:lechao_lcview_hal_service:s0
```

**为什么需要两条规则？**

`selabel_lookup` 在匹配服务上下文时会匹配含实例名后缀的完整服务名。HAL 注册的服务名为 `vendor.lechao.lcview.ILcView/default`（含 `/default` 实例后缀）。如果只注册 `vendor.lechao.lcview.ILcView` 不含 `/default`，SELinux 会拒绝服务注册（返回 `-1`）。详细原理见 11.06 相关文档。

### 3.4 配置项解析

| 配置项 | 位置 | 作用 | 不配的后果 |
|--------|------|------|-----------|
| `PRODUCT_PACKAGES += lechao_lcview lechao_lcview_hal` | device.mk | 打包进镜像 | 模块编译但不安装到 img |
| `PRODUCT_PACKAGES += vendor.lechao.lcview.ILcView-vintf` | device.mk | VINTF manifest 进 vendor 镜像 | HAL 不会在 VINTF 中声明 |
| `PRODUCT_PACKAGES += vendor.lechao.lcview-config` | device.mk | JSON schema 配置进 vendor 镜像 | daemon 启动时找不到 schema 文件 |
| `init.rc: mkdir /data/vendor/lechao_lcview` | lechao_lcview.rc | init 阶段创建数据目录 | daemon 无法创建日志文件 |
| `ueventd: /dev/vendor_lechao_lcview` | ueventd.rpi5.rc | 设置设备节点权限 | HAL 无法打开设备节点 |
| `file_contexts: /data/vendor/lechao_lcview(/.*)?` | sepolicy/ | 数据目录 SELinux 标签 | daemon 无法写入日志文件 |
| `service_contexts: vendor.lechao.lcview.ILcView` | sepolicy/ | Binder 服务 SELinux 上下文 | HAL 服务注册被 SELinux 拒绝 |

> 内核态 Builder API、环形缓冲区并发模型、GFP_ATOMIC 上下文安全等组件级设计详见 [01.01-内核态增强](./01.01-内核态增强-lcview-kernel.md) §4 过程视图。

## 相关资源

- [patchs/](../patchs/rpi5/) — 内核态+用户态完整源码（含 README 编译指南），其中：
  - `kernel/new/vendor/lechao/LcView/` — LcView 内核模块完整文件（+ 9 个 USB 打点事件）
  - `aosp/new/vendor/lechao/services/lechao_lcview/` — 用户态 HAL + Daemon 完整源码
  - `aosp/new/device/brcm/rpi5/sepolicy/` — SELinux 策略文件（lechao_lcview/lechao_lcview_hal domain）
- spec 文档：[`docs/superpowers/specs/2026-06-08-lcview-upload-design.md`](../../docs/superpowers/specs/2026-06-08-lcview-upload-design.md) — 上传器设计文档（二期）

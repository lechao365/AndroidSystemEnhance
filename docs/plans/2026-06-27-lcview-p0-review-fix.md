# LcView P0 检视修复执行计划

> **范围**：内核 + HAL + Daemon + sepolicy，共 10 项严重问题
> **批次**：P0（先修复严重项，验证通过后再启动 P1）
> **依据**：`01-打点增强/` 三份设计文档与 `patchs/rpi5/` 代码全链路检视

## 一、问题清单与方案

### 内核（5 项，改动文件：lcview_ring.c / lcview_internal.h / lcview_main.c / lcview_builder.c）

| ID | 问题 | 方案 |
|----|------|------|
| S1 | `lcview_ring_destroy` UAF：唤醒 reader 后立即 vfree，未等退出 | 引入 `atomic_t readers` 引用计数：open inc / release dec + wake；destroy 中 `wait_event(readers==0)` 再 vfree |
| S2 | `copy_to_user` 失败后 read_pos 已推进，记录丢失 | 锁内拷出 + 暂存 rpos → 锁外 copy_to_user → 成功才在下一轮锁内推进 read_pos；失败返回 -EFAULT 且 read_pos 未变 |
| S3 | `lcview_ring_write` 缺 len 上限校验，整数溢出 | 入口加 `if (len > LCVIEW_BUILDER_MAX_SIZE) return -EMSGSIZE` |
| S5-内核 | 4B/2B 长度前缀主机字节序，builder 注释与实现矛盾 | `cpu_to_le32/le16` 序列化；修正注释 big-endian→little-endian |

### HAL（3 项，改动文件：LcView.cpp / LcView.h / lechao_lcview_hal.rc / Android.bp / ILcView.aidl）

| ID | 问题 | 方案 |
|----|------|------|
| S6 | `aidl_interface` 缺 `versions`，未真正冻结 | 加 `versions: ["1"]`；执行 `m vendor.lechao.lcview-freeze-api` 生成 `aidl_api/.../1/` |
| S7 | readerLoop 异常退出后 HAL 僵死，daemon 无法感知 | 引入 `mReaderAlive`；致命错误 `exit(1)`；openDevice 加 5 分钟超限 exit；rc 移除 oneshot；退出前 closeDevice+notify_all |
| M-D1 | AIDL 未承诺 record 边界对齐 | ILcView.aidl getBatch 注释明确"返回值为完整 record 整数倍"；HAL flush 时断言 |

### Daemon（2 项，改动文件：FileWriter.cpp / SchemaParser.cpp）

| ID | 问题 | 方案 |
|----|------|------|
| S8 | openFile 不恢复 currentSize，轮转失效 | 打开后 `fstat` 恢复 `fs.currentSize = st.st_size` |
| S9 | SchemaParser 对 JSON 字段缺失无防御，可能 crash | 每关键字段 `isMember+isXxx` 校验，失败返回 false；外层 try/catch 兜底 |

### sepolicy（1 项，改动文件：lechao_lcview_hal.te / lechao_lcview.te）

| ID | 问题 | 方案 |
|----|------|------|
| S10 | 注释错误描述为"显示 HAL"；权限过度（含 write） | 注释改为"日志/事件打点"；权限收紧为 `{ open read ioctl getattr }`，移除 write |

### 字节序跨层（S5 延伸，改动文件：FileWriter.cpp / SchemaParser.cpp）

- Daemon 解析处配套 `le32toh/le16toh` 读取长度前缀

## 二、自主决策记录

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 内核版本 | 6.6.116（已查证 workspace Makefile） | `class_create(name)` 单参数自 6.4 起支持，S4 不成立 |
| 字节序 | 统一小端序（破坏性变更，patchs 阶段无历史数据） | 跨进程二进制协议必须固定字节序，Android 规范倾向 LE |
| 热重载 | P1 阶段删除 reload/closeFile 死代码 | YAGNI：schema 是 vendor 预编译，运行期不变 |
| readerLoop 故障 | exit(1) + 移除 oneshot | 符合 Android init 哲学，崩溃是显式信号 |
| Daemon 分区 | 保持 system 域，补 VINTF 合规核实（非代码） | system 域写 /data 是设计目标 |

## 三、验证策略

1. **内核**：`make O=$KERNEL_OUT Image dtbs`（Clang+LLD），编译通过无新 warning
2. **HAL/Daemon**：`m lechao_lcview lechao_lcview_hal vendor.lechao.lcview-update-api`，AIDL freeze 无 diff
3. **完整镜像**：`mk_rpi5_full_image.sh` 打包 bootimage/systemimage/vendorimage
4. **上板**：adb 验证服务注册、getBatch、轮转、invalid 日志

验证全部通过前禁止 `lc-sync-code-to-patchs` 归档（SRC-002）。

## 四、改动顺序

1. 内核 5 项（最复杂，先行）
2. HAL 3 项
3. Daemon 2 项
4. sepolicy 1 项
5. 文档同步 01.01/01.02/01.03
6. 编译验证

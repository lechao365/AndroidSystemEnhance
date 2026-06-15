# 设计规格：02.04/02.05 故障注入与验证文档重建

## 背景

将 AndroidSystemLearn/12-故障注入/12.01-IO故障注入 中的两个子文档（12.01.01 Host 端、12.01.02 Device 端）重建为 AndroidSystemEnhance/02-IO增强/ 下的 02.04 和 02.05，作为 IO 增强体系的外围验证组件。

旧文档基于已过期的代码编写（usb-verify 仅 4 命令、usb-fault-inject 仅 5 类故障），新 patchs 中代码已大幅演进（usb-verify 9 命令、usb-fault-inject 12 类故障 + expect 契约表），文档需完全基于新代码重建。

## 代码演进摘要

### usb-verify（Host 端，rpi5，~1199 行 / 18 文件）

| 维度 | 旧版 | 新版 |
|------|------|------|
| 命令数 | 4（reset/check/info/meta） | 9（stats get/reset, config get/set, event read/wait, check stats/event/degrade） |
| 事件 | 仅 ioctl GET_STATS | ioctl GET_STATS + poll+read 事件环形缓冲区 |
| 断言 | JSON --expect 全量比对 | 阈值断言（--stall-ge N 等） |
| 新增 | — | config 管理、degrade 检查 |

### usb-fault-inject（Device 端，rpi-zero2w，~1176 行 / 9 文件）

| 维度 | 旧版 | 新版 |
|------|------|------|
| raw-gadget.c | 811 行（完整 USB 枚举引擎） | 252 行（薄封装，复用 ConfigFS） |
| faults.c | 76 行 | 264 行（统一 handle_one_cbw 骨架） |
| 故障类型 | 5 类 | 12 类（F1-F12） |
| expect.c | 不存在 | 123 行（期望值契约表） |

## 文档清单

| 文件 | 说明 |
|------|------|
| `02-IO增强/02.04-故障注入工具-usb-fault-inject.md` | Device 端故障注入工具（Pi Zero 2W），先注入后校验 |
| `02-IO增强/02.05-故障注入验证-usb-verify.md` | Host 端故障校验工具（Pi 5） |
| `02-IO增强/README.md` | 更新，添加 02.04/02.05 索引 + 外围闭环架构 |

## 章节设计

### 02.04-故障注入工具-usb-fault-inject.md

| 模板章节 | 核心内容 |
|----------|----------|
| 概述 | usb-fault-inject 定位：Device 端 CLI，Raw Gadget + BOT 协议，12 类故障注入 |
| 用例视图 | 正常路径：stall/corrupt/timeout 注入→输出 expect JSON；异常路径：CBW 读取失败/UDC 占用 |
| 逻辑视图 | 三层分解（main→faults→raw-gadget），expect_table 契约，BOT 协议结构 |
| 过程视图 | handle_one_cbw 统一协议骨架，4 处故障注入点 |
| 开发视图 | 9 文件源码矩阵，模块依赖图，Makefile（arm-linux-gnueabihf） |
| 部署视图 | Pi Zero 2W 运行拓扑，Raw Gadget + ConfigFS 共存架构 |
| 关键设计与实现 | (1) Raw Gadget 薄封装策略 (2) 12 类故障 F1-F12 详解 (3) expect_table 契约 (4) 短传输 Residue |
| 接口参考 | 8 个 CLI 命令、11 个 raw-gadget API、BOT 协议字段表 |
| 附录 A | 硬件清单与物理拓扑（USB OTG、双板独立供电） |
| 附录 B | 系统刷写（Pi Imager + Raspberry Pi OS Lite 32-bit） |
| 附录 C | 串口连接配置（mini UART/PL011、core_freq=250、Flow Control=None） |
| 附录 D | DWC2 Device 模式开启 + Raw Gadget 可用性确认 |

### 02.05-故障注入验证-usb-verify.md

| 模板章节 | 核心内容 |
|----------|----------|
| 概述 | usb-verify 定位：Host 端 CLI，读 /dev/vendor_lechao_usbdX，9 命令体系 |
| 用例视图 | 正常路径：stats→reset→注入→check 流程；异常路径：超时/断言失败 |
| 逻辑视图 | 四层分解（cli→device→check→common），9 种命令枚举，报告数据结构 |
| 过程视图 | poll+read 事件轮询（clock_gettime 精确超时），断言三态模型 |
| 开发视图 | 18 文件源码矩阵，模块依赖图，Makefile |
| 部署视图 | Pi 5 运行拓扑，/dev/vendor_lechao_usbd* 依赖 |
| 关键设计与实现 | (1) 阈值断言引擎 (2) poll+read 事件机制 (3) degrade 检查逻辑 |
| 接口参考 | 7 个 ioctl 命令、9 个 CLI 命令、6 个 device API |
| 附录 A | 编译部署（交叉编译/SCP 安装） |

### README.md 更新

- 文档索引表添加 02.04/02.05 两行
- 系统架构 PlantUML 图中添加"外围验证组件"包（usb-fault-inject + usb-verify）
- 添加"故障注入闭环"段落：注入→检测→上报→校验全链路

## 12 类故障映射表（文档核心内容）

| ID | 名称 | CLI 命令 | 触发内核事件 | expect_table |
|----|------|----------|-------------|-------------|
| F1/F2 | stall | `stall --ep <in\|out>` | STALL+TRANSPORT_ERROR+RESET | error≥1,reset≥1,stall≥1 |
| F3 | timeout | `timeout --duration <ms>` | TIMEOUT+TRANSPORT_ERROR+RESET | error≥1,reset≥1,timeout≥1 |
| F4 | corrupt-cbw-sig | `corrupt --field cbw-sig` | DATA_CORRUPT+TRANSPORT_ERROR+RESET | error≥1,reset≥1,corrupt≥1 |
| F5 | corrupt-csw-sig | `corrupt --field csw-sig` | 同上 | 同上 |
| F6 | corrupt-csw-tag | `corrupt --field csw-tag` | 同上 | 同上 |
| F7 | corrupt-csw-status | `corrupt --field csw-status` | 同上 | 同上 |
| F8 | short | `short --bytes <n>` | DATA_CORRUPT+TRANSPORT_ERROR | error≥1,corrupt≥1 |
| F9 | abort | `abort --ep <in\|out>` | TRANSPORT_ERROR+RESET | error≥1,reset≥1 |
| F10 | hotplug | `hotplug --cycles <n>` | DEVICE_DISCONNECT+PROBE | 全-1(节点观察) |
| F11 | disconnect | `disconnect` | DEVICE_DISCONNECT | 全-1(节点观察) |
| F12 | degrade | `degrade --delay <ms>` | TRANSPORT_END(速率下降) | 全-1(rate_drop) |

## 不迁移内容

- 4 个 scenarios/*.sh 脚本（旧语法，不在新 patchs 中）
- fault-verify 二进制文件、usb-fault-inject 二进制文件
- .superpowers/ brainstorm 目录

## PlantUML 约束

遵循 rules/plantuml.md：禁止空图块、花括号占位符、条件块内 fork。

## 源码路径引用

- usb-verify: `patchs/rpi5/others/usb-verify/`
- usb-fault-inject: `patchs/rpi-zero2w/others/usb-fault-inject/`

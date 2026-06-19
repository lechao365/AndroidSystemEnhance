# Connection 域

`connection/` 承载 loop engineering 的连接基础设施：定义跨 provider 的协议契约、provider/device 配置语义，以及具体 provider 的实现。

## 目录结构

| 目录 | 职责 |
|------|------|
| [protocol/](./protocol/) | 只放协议文档（跨 provider 的契约），不绑定具体 provider 实现 |
| [profiles/](./profiles/) | 放 provider/device 配置语义，描述「如何理解这台板子」 |
| [providers/](./providers/) | 放具体 provider 实现（当前仅 `rp5-serial`） |

## 设计原则

1. **协议与实现分离**：`protocol/` 定义 host/client 之间的契约，provider 实现遵循但不内嵌协议定义。
2. **profile 与运行配置分离**：`profiles/` 描述设备语义（prompt marker、boot marker、timeout 等），provider 自身只保留最小运行配置。
3. **provider 自治**：每个 provider 同仓管理 host / client / shared / tests，运行位置可不同但代码集中。

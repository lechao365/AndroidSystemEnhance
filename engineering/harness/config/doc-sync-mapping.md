# Patchs → 技术文档映射规则

> **用途**：`sync-patchs-to-doc` 命令依据本表，将 `patchs/rpi5/` 的改动精准分发到对应技术文档目录。
> 新增特性时只需更新本文件，无需修改 `workflows/` 工作流。

## 映射表

按 patchs 路径特征（glob，大小写不敏感）匹配，**自上而下首条命中即归属**：

| 优先级 | patchs 路径特征 | 对应文档目录 | 说明 |
|--------|----------------|-------------|------|
| 1 | `**/LcView/**` | `01-打点增强/` | LcView 打点框架（内核/HAL/Daemon） |
| 2 | `**/lcview*` | `01-打点增强/` | LcView 相关服务/HAL/sepolicy |
| 3 | `**/LcIod/**` | `02-IO增强/` | LcIod IO 监控驱动（内核/HAL/Service） |
| 4 | `**/lciod*` | `02-IO增强/` | LcIod 相关服务/HAL/sepolicy |
| 5 | `others/usb-verify/**` | `02-IO增强/` | USB 验证工具（02.04/02.05） |
| 6 | `others/usb-fault-inject/**` | `02-IO增强/` | USB 故障注入工具（02.04） |
| 7 | `kernel/modified/drivers/usb/**` | `02-IO增强/` | USB storage notifier（IO 监控依赖） |

## 通用配置类（需 AI 读 diff 判断分发）

以下路径改动可能**同时涉及多个特性**，无固定归属，AI 必须读取 diff 正文后判断分发到哪份文档（可多份）：

| patchs 路径特征 | 判定方法 |
|----------------|---------|
| `aosp/modified/device/brcm/rpi5/device.mk.diff` | 读 diff：含 `lciod`→02，含 `lcview`→01，两者都有→两边都更新 |
| `aosp/modified/device/brcm/rpi5/manifest.xml.diff` | 读 diff：按声明的 hal/service 名称分发 |
| `aosp/modified/device/brcm/rpi5/*sepolicy*` | 读 diff：按 `.te` 策略对象名称（lciod_*/lcview_*）分发 |
| `aosp/modified/device/brcm/rpi5/BoardConfig.mk.diff` | 读 diff：按涉及的模块配置分发 |
| `**/Android.bp` / `**/Makefile` / `**/Kconfig` / `**/Kbuild` | 读 diff：按新增的模块名（LcView/LcIod）分发 |

## 兜底规则

- **未命中任何特征**的改动：AI 读取 diff 内容后自行判断归属；无法判断时在方案中标注"归属待定"，由用户指定。
- **跨特性改动**：同一文件改动涉及多个特性时，分发到所有相关文档目录，各文档只更新属于自身的部分。
- **新增特性目录**：未来新增 `03-*` 等特性时，在上表追加映射行即可。

## 文档目录清单

| 目录 | 特性 | 主要 patchs 来源 |
|------|------|-----------------|
| `00-环境准备/` | 环境搭建、构建、刷机 | 一般不随 patchs 变动（静态文档） |
| `01-打点增强/` | LcView 内核态/HAL/Daemon | `**/LcView/**`、`**/lcview*` |
| `02-IO增强/` | LcIod 内核态/HAL/Service + USB 工具 | `**/LcIod/**`、`**/lciod*`、`others/usb-*` |

> `patchs/rpi5/README.md` 的文件映射表更新走 `sync-code-to-patchs`，不纳入本流程。

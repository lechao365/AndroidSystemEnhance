# lciod 防护栏构建 + lcview 轻量收口 设计文档

> 日期：2026-08-27
> 状态：已获用户批准（轻量收口 / main 抽出 / 本地一次性编码三决策已确认）
> 关联规则：SRC-001~004、CXX-001~004

## 1. 背景与目标

最近 10 笔提交完成了 lcview 四层防护栏（单测 / 字节序契约 / 上板 verify 用例 / 验证收据）建设并经收口审计（9393dbf）无假绿。lciod 模块（用户态 1225 行 + 内核驱动 1992 行）目前完全空白：无 tests/、无测试 target、无 verify 用例、无 modules 登记。

目标：参考 lcview 模式为 lciod 构建同构防护栏，lcview 做轻量收口；全部防护栏验证 PASS 后作为 code 质量表征，满足 SRC-004 基线生成前置证据，由用户决策是否 promote。

## 2. lcview 现状评估结论（判定：PASS，仅轻量收口）

四层已完整：unit_test 4 文件 + hal_test 2 文件（1993 行，S5/S7/S8/S9 + CXX-004）；record_codec_test 契约快照；verify-cases 5 用例 26 判据 + lcview_check.py 8 模式；最新收据 pass。

残余缺口处置（轻量收口，不改 lcview 生产源码）：
| 缺口 | 处置 |
|---|---|
| 两个 main() 无单测 | 可接受，板级 svc/boot 兜底 |
| 事件 6/7/10/11/12 无触发 | 归因不变（打点在 USBD/LcIod 侧）；verify-cases 登记补充 lciod stats 维度交叉引用 |
| 事件 13 随机 | 维持"已观察待确定性触发" |
| rc/VINTF/config 无静态单测 | 可接受 |
| usbreset.c 无单测 | 可接受 |

lcview 唯一改动：AGENTS.md 测试门禁扩展为四 target。

## 3. lciod 防护栏分层设计

### 3.1 层 1：单测（新建 tests/，2 个 cc_test）

| 测试文件 | 目标源 | 防护点 |
|---|---|---|
| MinorUtils_test.cpp | common/minor_utils.cpp | ParseMinorFromPath 全边界（前缀/空后缀/非数字/±号/65535/ERANGE/minor=0 区分）、BuildDevicePath 往返 |
| DeviceIo_test.cpp | hal/device_io.cpp | pipe 注入真 syscall（对齐 lcview DeviceReader_test）：read_event 全分支（ETIMEDOUT/EIO-POLLHUP/多条排空留最新/部分读 EAGAIN/EBADF）、open_device 重试耗尽、close_device 幂等、get_stats 坏 fd memset 防御、list_devices 前缀、**ABI 契约快照**（sizeof stats=240/event=24/config=8、ABI_VERSION=2） |
| HalService_test.cpp | hal/hal_service.cpp | mDeviceMap 缓存一致性、未知 minor ENODEV、getStats 真实设备字段映射（vendor/product 非空 ≤32）、config 往返、readEvent 超时安全（板上 device-tests） |
| Service_test.cpp | daemon/service.cpp（抽 ComputeAverageRate/ComputeKbRate 纯函数） | 除零防护（totalNs=0）、公式正确性、KB/s 换算 |
| HalClient_test.cpp | daemon/hal_client.cpp（抽 RetryIntervalMs 静态纯函数，负数 clamp 防移位 UB） | 500ms×2^min(n,4) 封顶 5s 全分支 |

测试 target：`lechao_lciod_unit_test`（general-tests：MinorUtils/Service/HalClient + daemon filegroup）、`lechao_lciod_hal_test`（device-tests：DeviceIo/HalService + hal filegroup），均 native_coverage: true + libgmock。

### 3.2 生产源码配套重构（对齐 lcview 模式，低风险）

- **main 抽出**：hal_service.cpp main → `hal/main_lciod_hal.cpp`；service.cpp main → `daemon/main_lciod.cpp`（main 为纯组装代码）
- **类头文件抽取**（filegroup 复用前置条件，对齐 lcview LcView.h 模式）：
  - 新增 `hal/hal_service.h`：DeviceEntry + IoHalImpl 完整类声明；hal_service.cpp 改类外定义
  - 新增 `daemon/service.h`：IoServiceImpl 完整类声明 + ComputeAverageRate/ComputeKbRate 纯函数声明；service.cpp 改类外定义
- **hal_client.h** 加 `static int64_t RetryIntervalMs(int retryCount)`；hal_client.cpp get() 改调用（负数 clamp，行为不变）
- **filegroup**：daemon/Android.bp + hal/Android.bp 各加 filegroup（main 不入组），cc_binary srcs 改为 `main 文件 + :filegroup`

### 3.3 层 2：上板取数工具

新增 `tools/lciod_probe.c` + `tools/Android.bp`（cc_binary `lciod_probe`，落 /system/bin）：
- 枚举 glob /dev/vendor_lechao_usbd* → 逐节点 GET_STATS 打印单行 key=value（全 26 字段 + abi_version，vendor/product 引号包裹防空格破坏格式）
- `--reset`：打印前逐节点 RESET_STATE（trigger 用例 baseline 归零，delta 断言简化为绝对值）
- 设备侧最小操作 + host 复杂解析（防假绿原则同 lcview_check）

### 3.4 层 3：verify-cases 上板用例（3 条 + modules 登记）

| 用例 | 判据 |
|---|---|
| lciod-liveness | `svc:lechao_lciod_hal svc:lechao_lciod log:"monitor: minor=" boot`（监控线程每 10s 持续日志，防启动日志滚出） |
| lciod-pipeline | hostcmd `cases/lciod_check.sh --mode stats`：host 校验设备数≥1、abi_version==2、字段齐全、数值非负、vendor/product 非空 |
| lciod-trigger | adb root → baseline --reset（归零+快照）→ dd 读 4MB → delta read_bytes 增量 → dd 写 1MB → delta write_bytes 增量（dd 失败即判红，无 \|\| true；块设备 env 可覆盖同 lcview-transfer） |

modules.lciod：targets（lechao_lciod/lechao_lciod_hal/IIoHal-vintf/lciod_probe）+ test_targets（2）+ push 映射（daemon/probe→/system，hal→/vendor）。

### 3.5 层 4：门禁与登记收尾

- AGENTS.md：`make lechao_lcview_unit_test lechao_lcview_hal_test lechao_lciod_unit_test lechao_lciod_hal_test -j$(nproc)`
- manifest.yaml：追加 lciod tests/tools 条目（重生成或手工对齐 lcview 段格式）
- harness 配套 pytest：`tests/test_lciod_check.py`（解析/校验纯函数级，不依赖设备）
- lcview 事件登记：verify-cases 注释区补充 lciod stats 维度交叉引用

## 4. 实施与验证（本地环境，不走 cross-device）

一次性完成全部编码（用户已确认），验证链路：
1. harness pytest 全绿（本地）
2. `/workspace-verify`：code→workspace 同步 → `make` 四 test target + 2 product target 增量编译 → 上板 push → verify-cases 全用例 → 收据落盘
3. 全 pass 后具备 SRC-004 证据链，基线决策权在用户

## 5. 风险与回退

- lciod-trigger 的 usbd 内核统计是否覆盖 dd 块设备流量为待实测项：若 read_bytes 不增，判红暴露并归因登记（防护栏暴露问题即其价值），fallback 归因后降级为 liveness+pipeline
- main 抽出/类外定义为结构性重构：行为零变更，编译 + 上板 svc/liveness 判据兜底
- 回退：dev 分支 git revert 单批即可，不影响 main

## 6. 缺口审计补充（2026-08-27 二次盘点，已补齐 2 处）

首轮验证 PASS 后二次盘点发现 lciod 2 处真实遗漏并补齐（lcview 无对应缺口）：

- **缺口①（已补齐）**：lciod_check.py stats 校验对 `enabled` 只查"存在且≥0"，
  enabled=0（监控被禁用）仍判绿——假绿点。已加 `enabled==1` 断言 + 2 测试用例
  （对齐 lcview logfield 故障可见性语义）。
- **缺口②（已补齐）**：lciod-pipeline 走 lciod_probe 直接 ioctl 内核，绕过
  daemon→HAL 的 AIDL 链路；而 lcview pipeline 是端到端的（JSONL 由 daemon 经
  AIDL 落盘）——lciod 的 IIoService 代理转发/投影无端到端断言，与 lcview 不对称。
  已按确认方案抽投影纯函数 `ProjectSystemIoStats/IoConfig/IoEvent`（service.h，
  从 getIoStats/getIoConfig/readIoEvent 抽出，行为零变更）+ Service_test 新增
  4 用例（21 字段逐一直传 + 管理字段省略 + 脏值覆盖 + 1:1 直传），防字段串位/漏投影。
  AIDL 链路连通性由 lciod-liveness 监控日志（daemon 经 AIDL readEvent/getStats
  持续成功）兜底，未新增 AIDL 客户端工具（成本/收益不匹配）。

## 7. 两模块共同的结构性缺口（非本次范围，判可接受）

| 缺口 | 处置 |
|---|---|
| 内核侧零单测（lcview 内核 6 文件 + LcIod 内核 2 大文件共 ~4000 行） | 靠板级 pipeline/trigger 集成判据兜底；内核单测需 KUnit 基建，另立项 |
| 两个 main() 组装代码无单测（lcview 2 + lciod 2） | 板级 svc/boot 判据兜底，判可接受 |
| rc/VINTF 无静态校验 | 板级 svc/boot/schema 运行时判据兜底 |
| 工具无单测（usbreset.c / lciod_probe.c） | 板级实测兜底（probe 即 pipeline 取数入口） |
| lcview 事件 6/7/10/11/12/13 无确定性触发 | 归因登记（打点在 USBD 栈/xhci-host 不经过）；lciod stats 维度互补 |

# lciod 防护栏构建 + lcview 轻量收口 实施计划

> 日期：2026-08-27
> spec：docs/superpowers/specs/2026-08-27-lciod-guardrails-design.md
> 执行方式：本地一次性编码 → harness pytest → workspace-verify 上板验证

## Step 1 — lciod hal 侧重构 + 测试

1. 新增 `hal/hal_service.h`：DeviceEntry + IoHalImpl 完整类声明
2. 改 `hal/hal_service.cpp`：include 自身头，9 处类外定义（IoHalImpl:: 前缀），删 main
3. 新增 `hal/main_lciod_hal.cpp`：main 移入
4. 改 `hal/Android.bp`：加 filegroup `lechao_lciod_hal_sources`（hal_service.cpp+device_io.cpp），cc_binary srcs → `["main_lciod_hal.cpp", ":lechao_lciod_hal_sources"]`
5. 新增 `tests/DeviceIo_test.cpp`：pipe 注入 + ABI 快照
6. 新增 `tests/HalService_test.cpp`：缓存一致性 + ENODEV + 字段映射（device）
7. 改 `common/Android.bp`：确认/补 export_include_dirs（供测试链接头文件）

## Step 2 — lciod daemon/common 侧重构 + 测试

1. 新增 `daemon/service.h`：IoServiceImpl 完整类声明 + ComputeAverageRate/ComputeKbRate 声明
2. 改 `daemon/service.cpp`：include 头，类外定义，getAverageRate/calc_rate 改调纯函数，删 main
3. 新增 `daemon/main_lciod.cpp`：main 移入
4. 改 `daemon/Android.bp`：filegroup `lechao_lciod_daemon_sources`（service.cpp+hal_client.cpp），cc_binary srcs → `["main_lciod.cpp", ":lechao_lciod_daemon_sources"]`
5. 改 `daemon/hal_client.h/.cpp`：加 static RetryIntervalMs（负数 clamp），get() 改调用
6. 新增 `tests/MinorUtils_test.cpp`、`tests/Service_test.cpp`、`tests/HalClient_test.cpp`
7. 新增 `tests/Android.bp`：lechao_lciod_unit_test（general-tests）+ lechao_lciod_hal_test（device-tests）

## Step 3 — 取数工具与上板用例

1. 新增 `tools/lciod_probe.c` + `tools/Android.bp`（lciod_probe → /system/bin）
2. 新增 `harness/skills/workspace-verify/cases/lciod_check.sh`（包装：adb connect+root+exec py，照 lcview_check.sh）
3. 新增 `cases/lciod_check.py`：stats/baseline/delta 三模式，退出码 0/1/2
4. 新增 `harness/skills/workspace-verify/tests/test_lciod_check.py`
5. 改 `harness/config/verify-cases.yaml`：+lciod-liveness/lciod-pipeline/lciod-trigger 三用例 + modules.lciod 登记 + lcview 事件登记交叉引用修订

## Step 4 — 门禁与登记

1. 改 `AGENTS.md`：测试防护命令扩四 target，测试源码路径补 lciod
2. 改 `code/rpi5/manifest.yaml`：lciod 段追加 tests×7（6 源文件+bp）+ tools×2 + main×2 条目

## Step 5 — 验证

1. 本地：`python3 -m pytest harness/skills/workspace-verify/tests/ -q` 全绿
2. workspace-verify：code→workspace 同步 → `make lechao_lciod lechao_lciod_hal lciod_probe lechao_lciod_unit_test lechao_lciod_hal_test vendor.lechao.lciod.IIoHal-vintf -j$(nproc)` 增量编译零错误（连同 lcview 两 test target 回归）
3. 上板：push 产物 → atest 两 lciod test target → verify-cases lciod-liveness/pipeline/trigger 全绿（lcview 五用例回归）
4. 收据落盘 data/verify/ + trend.md 更新

## 回退

dev 分支单批 git revert；lciod 生产源码重构行为零变更由 liveness 判据兜底。

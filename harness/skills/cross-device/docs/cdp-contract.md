# CDP 契约（cross-device prompt batch）

> **CDP-001**：本文档与 `../lib/python/cdp_parse.py` 必须成对修改，禁止单独改一边。

## 格式

    -s/-sv base:<12hex>
    意图: <这一轮要达成什么>
    验收: <判据>          (-s 必须为「无」；-sv 必须非空且不得为「无」)
    方向: <实施方向/约束>

## 规则

| 项 | 规则 |
|---|---|
| 模式 | `-s` 仅代码改动无上板验证；`-sv` 需上板验证 |
| base | 12 位 hex，= emit 产批时 origin/dev HEAD 前 12 位；apply 以 `--expect-base $(git rev-parse --short=12 HEAD)` 比对，不匹配整批拒绝（exit 18） |
| 三标签 | 必填各占一段，且不得重复（重复标签报 11 结构错误，emit/apply 均 blocking）；标签顺序不强制 |
| 预算 | 总字符 50~500（含首行） |
| batch_id | 规范化文本（剥 BOM/strip/去空行/LF，逐行删净行内空白）sha256 前 12 位 |
| 验收标签 | 推荐格式（非强制）：`svc:<svc>` 服务运行 / `log:<kw>` logcat 命中 / `prop:<k>=<v>` / `file:<path>` 存在 / `cmd:<无空格 shell>` exit 0 / `boot` boot_completed；允许自由文本由 AI 判断；含空格/引号命令走 `--case <标签>`（批次内禁引号，此类用例在 verify-cases.yaml 集中维护） |

## 退出码

0 通过 / 3 参数错误·文件不可读或非 UTF-8 / 11 结构错误（含未知行）/ 12 空批 / 14 三标签缺失 /
15 base 非法 / 16 预算超限 / 17 验收规则违规 / 18 base 不匹配
（emit 全 blocking；apply 仅对 17 降级 WARN，16 双角色 blocking）

## 收据字段：timings（链路耗时打点）

- 位置：`data/verify-results/<ts>-<batch_id>.md` header `timings` 字段（可选，缺省空串）
- 来源：apply 侧 `cdp_timing.py` start/mark 采集（precheck/edit/verify 内部各段），
  `ws_report.py --timings-file` 经 `compute_segments` 计算段耗时写入
- 结构：`{"batch_id": ..., "wall_start": ..., "wall_end": ..., "segments":
  [{"name": <阶段名>, "elapsed_s": <秒>}, ...]}`；loop 多轮时阶段名带 `run_<n>_` 前缀
- 语义：诊断数据非验收证据——缺失/非法仅 warn 不阻断 push 主流程（区别于 `--acceptance` 返 2）
- 消费：emit 侧复盘读 timings 定位耗时瓶颈（build/单测/验收哪段慢、重试轮数）
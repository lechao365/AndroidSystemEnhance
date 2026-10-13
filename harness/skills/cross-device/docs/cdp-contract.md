# CDP 契约（cross-device prompt batch）

> **CDP-001**：本文档与 `../lib/python/cdp_parse.py` 必须成对修改，禁止单独改一边。

## 格式

    -s/-sv base:<12hex>
    checksum: <16hex>    （可选元数据行，须紧跟首行；emit 产批经
                         cdp_parse.py --gen-checksum 生成）
    意图: <这一轮要达成什么>
    验收: <判据>          (-s 必须为「无」；-sv 必须非空且不得为「无」)
    方向: <实施方向/约束>

## 规则

| 项 | 规则 |
|---|---|
| 模式 | `-s` 仅代码改动无上板验证；`-sv` 需上板验证 |
| base | 12 位 hex，= emit 产批时 origin/dev HEAD 前 12 位；apply 以 `--expect-base $(git rev-parse --short=12 HEAD)` 比对，不匹配整批拒绝（exit 18） |
| checksum | 头部元数据行（紧跟首行），值 = 首行（mode+base）与正文（checksum 行以下全部行）规范化后整体 sha256 前 16 位；**首行纳入覆盖**（防 `-sv`→`-s` 等 mode/base 篡改静默过 checksum）；emit 产批经 `--gen-checksum` 生成（先规范化再定位首行，与解析口径对称），apply 侧解析存在即校验，不符整批拒绝（exit 1，双角色 blocking，防传输篡改/损坏）；无 checksum 行的旧批次 warn 兼容放行。他处出现 checksum 行按未知行报 11。结构错误消息中的行号为规范化后行号（与原始文件行号可能不一致） |
| 三标签 | 必填各占一段，且不得重复（重复标签报 11 结构错误，emit/apply 均 blocking）；标签顺序不强制 |
| 预算 | 总字符 50~500（含首行；checksum 行为机器元数据不计入，防 500 上限被挤占）；**位置词（文件路径/行号锚）与安全保护条件（越界/校验/回退等）属硬内容，压缩时只砍范围描述，不得砍位置词与保护条件**——方向含架构词（共用/抽取/拆出）时无行号锚会在 emit selfcheck 触发漂移 warn（见 cdp_parse.py direction_drift_warn，CDP-001 成对评估：预算校验仍只查字符数，parser 无需改） |
| 引号禁令 | 批次正文禁用单双引号字符（' 与 "，emit 角色校验，违规 exit 19）——apply 侧传输层会展开吞字致批次结构损坏；改用中文标点（「」、——）或去引号 |
| batch_id | 规范化文本（剥 BOM/strip/去空行/LF，逐行删净行内空白）sha256 前 12 位 |
| 验收语法 | `-sv` 验收必须为 `case:<id>[,<id>...]`（id 限小写字母数字与连字符，多个用逗号分隔，逐个查 verify-cases.yaml cases 段，任一未知判死）或 `manual:<自由文本>`（**仅 manual 模式保留自由文本**）；用例 id 在 verify-cases.yaml 集中维护，批次内不再书写 svc/log/prop/file 等验收表达式。**用例两级策略（B6）**：`-sv` 常态回归批验收 case 默认取快速回归组（lcview-liveness, lcview-pipeline, lcview-trigger, lciod-liveness, lciod-trigger）；publish-main-base 前的全量验收批取全部 case；批次方向涉及特定 case 的专项修复按需追加——选择依据见 verify-cases.yaml 顶部注释 |
| 方向编号 | 多方向时以「1 xxx 2 yyy 3 zzz」连续编号；**1 须在行首或句号/分号后，后续连续编号可空格分隔，每个编号后须带边界（空白 / `)` `）` `、` / 句点 `.` / 句号 `。`）**（ws_report 方向数解析与 CDP-DOD-003 逐方向自报条数门禁据此计数：只认从 1 起的最长连续编号链；小数（1.5）与「9 处」「15s」等中文计数不计，分号/空格格式批次曾被解析为 0 绕过门禁或误报） |
| apply git 门禁 | `--role apply` 在结构/base 校验前机器校验「分支为 dev」+「工作树干净（`git status --porcelain` 为空）」：分支非 dev / 工作树脏 / git 命令失败均拒（exit 20，fail-closed）——原 cdp_apply_precheck 的这两项判定纳入实际 apply 入口，防退化为人工纪律。`--root <仓库根>` 缺省 cwd（生产从仓库根执行），仅供测试注入 |

## 退出码

0 通过 / 1 checksum 不符（篡改/损坏，双角色 blocking）/ 3 参数错误·文件不可读或非 UTF-8 / 11 结构错误（含未知行）/ 12 空批 / 14 三标签缺失 /
15 base 非法 / 16 预算超限 / 17 验收规则违规 / 18 base 不匹配 / 19 引号违规（仅 emit）/ 20 apply git 环境门禁（分支非 dev / 工作树不干净 / git 命令失败）
（emit 全 blocking；apply 仅对 17 降级 WARN，16/1/18 双角色 blocking，19 仅 emit 校验，20 仅 apply 角色触发——apply git 环境门禁）

## 收据字段：timings（链路耗时打点）

- 位置：`data/verify-results/<ts>-<batch_id>.md` header `timings` 字段（可选，缺省空串）
- 来源：apply 侧 `cdp_timing.py` start/mark 采集（precheck/edit/verify 内部各段），
  `ws_report.py --timings-file` 经 `compute_segments` 计算段耗时写入
- 结构：`{"batch_id": ..., "wall_start": ..., "wall_end": ..., "segments":
  [{"name": <阶段名>, "elapsed_s": <秒>}, ...]}`；loop 多轮时阶段名带 `run_<n>_` 前缀
- 语义：诊断数据非验收证据——缺失/非法仅 warn 不阻断 push 主流程（区别于 `--acceptance` 返 2）
- 消费：emit 侧复盘读 timings 定位耗时瓶颈（build/单测/验收哪段慢、重试轮数）
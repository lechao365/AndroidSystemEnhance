# workspace-verify 循环引擎（loop-engineering）吸纳设计

- 日期：2026-08-30
- 状态：待评审
- 范围：新增 `harness/skills/loop-engineering/` skill；修订 cross-device-apply 与 workspace-verify 两个 SKILL.md 的职责边界；不改任何既有脚本的行为契约

## 1. 背景与目标

### 1.1 现状问题（调查实证）

| # | 问题 | 证据 |
|---|------|------|
| P1 | 验证重试计数只存在于 AI 会话上下文，上下文压缩/会话死亡即清零 | ws_report 每批只写一张收据，中间自愈轮零落盘 |
| P2 | 双散文循环并存：apply 编辑自愈"上限 3 次"（apply SKILL L29）+ verify 验证自愈"上限 3 次"（verify SKILL L36），边界仅靠文档区分 | 两个 SKILL.md |
| P3 | 循环不可观测：data/verify 现存 51 份收据 0 份 fail，重试是否发生过不可考 | trend.md 全 pass/skip |
| P4 | verify 会话既当执行编排又当失败分析，重失败日志全部灌进执行会话 | verify SKILL L36 自愈散文 |
| P5 | 纠错大量发生在"下一批"（emit 复盘重新产批），同轮修复能力未被结构化利用 | trend 中"修 XX 假绿"式连续批次 |

### 1.2 借鉴来源与目标

吸纳 LcSkills `lc-skills-loop-engineering` 的循环工程思想（脚本管状态记账、AI 管语义修复、超限出结构化诊断），但以本仓既有资产（收据链、ws_acceptance 标签 DSL、三级连接通道）为原语重建，**不引入任何外部依赖**（LcSkills 仓库将删除）。

目标：

1. 重试状态脚本化持久，会话中断可 resume
2. 三层职责正交：apply（批次代理）/ loop（收敛管理）/ verify（原子执行）
3. 失败可归因：区分框架问题与 LLM 无法解决的任务问题
4. 双入口：跨设备（apply->loop->verify）与本地（loop->verify）均支持
5. 业务流程行为等价，任何时刻可降级回基线行为

## 2. 三层架构

```
apply（弱模型，批次代理）    loop（收敛管理器）             verify（原子执行器）
──────────────────────     ─────────────────────        ─────────────────────
批次解析/门禁/编辑 code/ ->  session 状态机            ->  同步->编译->推送->验收
拆解 loop 输入(四字段)       patience/total 双计数          一次验证 = 一张收据
编辑失败自愈(不产收据)       失败分析/修复编辑              收据内部恢复(rescue 等)
收尾: git-works-push        退出协议 + 诊断报告            无自愈散文
     ↑                                                     │
     └── 收据+诊断原样上传 -> emit 强 LLM 复盘产新批 ─────────┘（跨设备大循环，不归 loop）
```

**单一划分判据：以"收据落盘"为循环原子。** 产生收据的轮次归 loop 记账；收据内部的恢复（rescue/时钟校准）留 verify；收据之前的编辑留 apply；收据之后的分析与修复归 loop。

## 3. 职责边界（逐项裁决表）

| 职责 | 归属 | 说明 |
|------|------|------|
| 存批次（heredoc 纪律）/ dev+干净树+base 门禁 / precheck | apply（不变） | 跨设备入口与批次守门 |
| 批次编辑 code/（hunk 约束 + validate_patch + manifest 重生成） | apply（不变） | emit 契约执行者 |
| 编辑失败自愈（上限 3 次） | apply（保留散文） | 不产收据不进 loop；机械信号即校验 exit code |
| -s 批次直写 skip 收据 | apply（不变） | 无验证无循环 |
| 拆解 loop 输入 | apply（新增，纯透传） | 四字段：goal/batch-file/target（case 从批次解析） |
| -sv 拉起验证 + 收尾 git-works-push | apply（拉起对象 verify->loop） | 编排出口与 push 内容不变 |
| session 状态机（start/done/status） | loop（新增） | attempt 推进唯一锚 = 收据落盘成功 |
| 拉起单次 verify / ws_report 返 2 补参重试 | loop | 补参不产收据不耗轮次 |
| 读收据失败分析 + 砖机三分法判定 | loop（自 verify 上移） | 判定是跨轮决策输入 |
| 修复编辑 code/（验证失败驱动） | loop（自 verify 自愈散文上移） | 复用 apply 机械护栏：改 .diff 跑 validate_patch；改 code/rpi5 跑 gen-manifest-only |
| 重试/退出决策 | loop | 集中一处，散文消亡 |
| 诊断报告聚合 + 并入末轮收据 --body | loop | 随批入库，emit 复盘可见"本地已证伪方向" |
| verify 步骤 1-6（同步/编译/推送/验收/收据） | verify（不变） | 6 脚本与 CLI 契约零改动 |
| rescue 一次 / 时钟校准 / 三级连接 / ai 三态 / 高危确认门 | verify（不变） | 单次收据内部恢复与判定 |
| 失败现场抓取（logcat/dmesg/串口摘录入 body） | verify（不变） | 工具脚本（ws_serial.py）留 verify 目录，判定归 loop 会话 |
| verify 自愈散文（SKILL L36） | 删除 | 上移 loop，一次改齐 |

### 3.1 各层禁止事项

- **apply 禁**：读 verify 失败现场做分析；批次编辑之外的二次编辑；维持重试计数（发现未终结 session 时交 loop resume）
- **loop 禁**：解析/编辑批次内容（批次契约归 apply）；嵌套自愈循环（重试只有一层）；写收据（收据只由 verify 单次执行终点产生）；push/commit（上传归 apply）
- **verify 禁**：跨轮决策（重试与否、修复方向）；修改 code/（执行器不自愈源码）

## 4. loop 会话状态机

### 4.1 session.json 结构

落盘 `harness/log/loop-engineering/session-<id>/session.json`（gitignore 工作态）：

```json
{
  "id": "<12hex>",
  "mode": "A|B",
  "goal": "<目标文本>",
  "batch_file": "<cdp 路径，模式 A>",
  "target": "<12hex|dev|main，模式 B>",
  "case": "<验收用例标签，模式 B>",
  "created_at": "<ISO8601>",
  "updated_at": "<ISO8601>",
  "patience": 0,
  "max_patience": 3,
  "total_attempts": 0,
  "max_total": 10,
  "exit_attribution": null,
  "runs": [
    {
      "attempt": 1,
      "ran_at": "<ISO8601>",
      "receipt_path": "data/verify/<...>.md",
      "result": "pass|fail",
      "stage": "sync|build|push|acceptance",
      "verify_exit": 0,
      "fingerprint": "<归一化哈希>",
      "fingerprint_frozen": false,
      "attribution": "task_fail|env_fail|param_error|framework_error|pass",
      "fix_action": "<本轮修复动作摘要>",
      "log": "attempt-1.log"
    }
  ]
}
```

每轮快照冗余存收据头关键字段（result/build/push_board/失败 tag 摘录）——收据老化（50 份配额）淘汰后 session 仍自洽，聚合诊断读快照不读文件。

### 4.2 双层计数（核心语义）

| 计数器 | 语义 | 推进/清零 | 默认上限 |
|--------|------|-----------|----------|
| `patience` | **同一问题的连续修复失败次数** | 收据 fail 且指纹与上轮**冻结** -> +1；指纹**演化**（新问题/新阶段暴露）-> **清零** | 3 |
| `total_attempts` | 会话总轮次（成本护栏） | 每轮 +1，不清零 | 10 |

- 10 阶段任务各失败 1 次 = 指纹每轮演化 = patience 清零 = 继续推进（仅 total +1）
- 同一指纹连续 3 次冻结 = 同一问题修不动 = 退出升级 emit
- 双层缺一不可：只有 patience 会被签名漂移无限拖延（误判演化），只有 total 会误杀长任务
- 业界对照：patience 采 early-stopping/no-progress-detection 模式；total 采 agent loop max_iterations 模式

### 4.3 失败指纹（归一化）

三元组 `(失败阶段, verify exit code, 首错误行归一化哈希)`。归一化规则：剥时间戳、绝对路径（家目录/workspace）、内存地址、行内偏移；保留错误类别 + 稳定错误消息。归一化函数配单元测试（含已知误判样本：时间戳漂移、路径前缀差异）。

### 4.4 归因分类学（每轮强制记录）

| 归因 | 判定信号（机械可判） | loop 动作 |
|------|----------------------|-----------|
| `pass` | 收据 result=pass | 成功退出 |
| `task_fail` | 收据 result=fail 且 build/board/acc 有真实判红 | 修复重试（耗轮次） |
| `env_fail` | 设备不可达/砖机三分法（串口静默/半砖/boot loop，body 有现场） | **不烧修复轮**：early-exit 归因 env 转人工/emit |
| `param_error` | verify exit 2、无收据 | 补参重跑，不耗轮次 |
| `framework_error` | harness 脚本 traceback / 契约外退出码 / 收据该落未落 | **立即停环**，标记人工介入 |

**红线：framework_error 禁止重试**（重试会把框架 bug 洗成"LLM 修不动"的假象）；env_fail 不烧轮次同理（代码修复对砖机无效，烧轮次污染升级信号）。

### 4.5 退出协议

| 退出归因 | 触发 | 交付物 |
|----------|------|--------|
| `task_unsolvable` | patience 耗尽且指纹冻结 | 末轮收据（body 含诊断）+ 诊断报告 |
| `cost_cap_exceeded` | total_attempts 达上限 | 同上 + 各阶段推进轨迹（区分"任务确实长"vs"修复在绕圈"） |
| `env_fail` | 砖机/设备不可达 | 末轮收据（body 含串口现场） |
| `framework_error` | 脚本异常 | 尽力落收据 + 异常现场；无收据时如实标记 |
| `pass` | 收据 pass | 收据路径 + attempt 数 |

回传上游（apply 或直接用户）：`最终收据路径 + result + 退出归因 + attempt 数`，互不渗透上下文。

### 4.6 诊断报告（diagnosis.md）

落 `harness/log/loop-engineering/session-<id>/diagnosis.md`，**同时并入末轮收据 --body**（否则 emit 侧拿不到）。首行结论四分类；含：结论 / 各轮收据快照明细（stage/指纹/归因/修复动作）/ 指纹演化轨迹 / 已证伪修复方向 / 建议（升级 emit 时附"本地已试过"清单，强模型不重复撞死胡同）。

### 4.7 会话工件老化

- 老化对象：`harness/log/loop-engineering/session-<id>/` 目录（一个目录一份，含 session.json + attempt-N.log + diagnosis.md）
- 配额 `_SESSION_KEEP = 20`：超过删最旧（按目录时间戳升序）；`ws_session.py start` 时写时老化（同 cdp_receipt.write_receipt + prune_details 模式）
- **活跃会话保护**：仅删除已终结目录（session.exit_attribution 非空）；若全部活跃则不删并 WARN（防止正在跑的会话被中途蒸发）
- **安全前提**：诊断已并入末轮收据 body 随批入库（§4.6），session 老化不丢跨批证据；收据老化（50 份）与 session 老化（20 份）互不耦合，各自独立淘汰
- resume 语义与老化兼容：被老化的 session 不可 resume，但按目录序先淘汰最旧已终结会话，活跃与近期会话总在窗口内

## 5. 双入口

| | 模式 A（跨设备） | 模式 B（本地，不经 apply） |
|---|---|---|
| 触发 | apply 拆解批次后拉起 | 用户自然语言触发（"验证 liveness"/"跑一次恢复验证"），AI 拆解为 --target/--case/--goal |
| 输入 | `--batch-file <cdp>` | `--target <12hex|dev|main> --case <标签> --goal "<目标>"` |
| 验收来源 | 批次验收文本（cdp_parse 解析） | verify-cases.yaml --case（既有语义，零新增） |
| 收尾 | apply 收末轮收据+归因 -> git-works-push | loop 会话直接汇报；需入库时走 git-works-push |

**模式 B 独立性保证**（不依赖 apply 的任何组件）：

1. 修复编辑的机械护栏（cdp_validate_patch.py / gen-manifest-only）为 harness 共享脚本，loop 直调，非 apply 私有
2. 验收来源（verify-cases.yaml）、报告（ws_report）、连接（ws_adb_connect）均为 verify 侧既有资产
3. 模式 B 的输入拆解极薄（三字段，AI 从用户话语直接映射），无批次解析环节
4. 收尾可零 git（人工验证场景不强制 push），与会话管理解耦

两类场景同一状态机、同一计数/归因/退出协议、同一诊断产物--仅入口与收尾不同。模式 B 典型场景：revert 恢复验证（成熟后自 revert 步骤 4 切入）、手工回归、性能采集（lcview-perf）、环境冒烟。

## 6. CLI 接口契约

新增 `harness/skills/loop-engineering/ws_session.py`（单脚本，只管状态不管执行）：

```
ws_session.py start --goal <文本> [--batch-file <cdp> | --target <12hex|dev|main> --case <标签>]
                      [--max-patience 3] [--max-total 10]   -> 创建 session（幂等：同 goal+target 已存在则复用）
ws_session.py run   --session <json>                        -> 拉起一次 verify（模式 A 解析批次验收；模式 B 用 session
                      记录的 --case/--target）
                      内部：调 verify -> 落收据 -> 记录轮次 -> 输出下一步指令
ws_session.py status --session <json>                       -> 一站式：当前轮/各轮归因与指纹/计数器/末错误/三层日志路径/下一步
ws_session.py done   --session <json> --receipt <路径> [--stage <sync|build|push|acceptance>]
                       [--error-line <首错误行>] [--attribution env_fail|framework_error]
                       -> attempt+1 + 指纹比对 + 归因 + 计数推进（result 取自收据，非命令行传入；
                          attempt 唯一推进锚 = 收据落盘成功）
ws_session.py diagnose --session <json>                     -> 聚合诊断报告（超限或手动触发）
```

退出码：0 正常 / 1 会话状态错误 / 2 参数错误 / 3 session 文件非法。

**loop SKILL.md 的 AI 职责**：生成/维护 goal 语义、每轮收据失败分析、修复编辑决策、`task_fail` vs `env_fail` 判定复核（机械判定优先，AI 仅在边界 case 复核）、诊断报告语义部分撰写。脚本职责：状态记账、指纹归一化比对、计数推进、退出判定、报告骨架生成。

## 7. 与收据链的耦合语义（不变式）

1. 每轮 attempt 落一张收据（同 batch_id、verified_commit 全批一致、时间戳递增）——已实证对 promote（latest-wins + HEAD^ 比对）与 emit precheck（ancestor 比对）安全
2. trend 中间 fail 行 = 可审计性提升（"该批重试 N 轮"可查询），非污染；可选微调 `_DETAIL_KEEP` 50->75
3. -s skip 路径不经 loop（apply 直写）
4. session 只引用收据路径不复制（快照冗余除外）
5. session 生命周期 = 单批验证会话；跨批（emit 新批）= 新 session

## 8. 日志与可定位性

```
harness/log/cross-device-apply/              # 既有
harness/log/loop-engineering/session-<id>/   # session.json + attempt-N.log + diagnosis.md
harness/log/workspace-verify/                # 既有
```

- 每行日志带 session id（模式 A 同时记 batch_id），一个 ID 贯穿三层可 grep
- `attempt-N.log`：完整调用命令、exit code、收据路径（或"无收据+原因"）、指纹、修复动作摘要
- `status` 目标：30 秒定位问题层（框架/环境/任务）
- 三层 SKILL.md 各写接口契约，接口变更必须成对改

## 9. 降级语义（任何时刻不 break）

| 异常 | 行为 |
|------|------|
| session 文件丢失/损坏 | 退化为直接调 verify = 基线行为（SKILL 明示回退路径） |
| loop 脚本异常 | framework_error 停环，apply 直调 verify 兜底 |
| 指纹误判（漏判冻结） | total_attempts 护栏兜底；诊断报告供人工复核 |
| 中间轮收据被老化淘汰 | session 快照自洽，诊断仍可聚合 |

## 10. 业务流程等价性核对（现状 vs 新）

| 步骤 | 现状 | 新 | 差异 |
|------|-----|-----|------|
| emit 产批 | precheck+产批+selfcheck | 同 | 零（诊断报告是复盘可选输入） |
| apply 门禁/编辑/编辑自愈 | 同 | 同 | 零 |
| -s 分支 | 直写 skip 收据 | 同 | 零 |
| -sv 首次验证 | apply 拉起 verify | apply 拉起 loop -> loop 首调 verify | 行为等价 |
| 验证失败 | verify 会话散文自愈（上限 3） | loop 同语义重试（patience 3） | 语义连续，纪律脚本化 |
| 收据 | 每批 1 张 | pass 批 1 张 / fail 批 N 张 | 门禁已实证安全 |
| push | 收据+代码 | 同（诊断在收据 body 内） | 零 |
| promote/revert | 同 | 同（revert 兼容期直调 verify 模式 B，成熟后可切 loop） | 零 |

## 11. 明确不做（范围裁决）

- **不采纳** on_fail collectors（中间轮失败现场仍 verify 收据 body 承载，仅超限时 loop 聚合）
- **不采纳** fixture 离线回放（workspace-verify 现有单测已覆盖同等需求）
- **不采纳** validate 用例校验子命令（verify-cases.yaml 结构稳定，风险低）
- **不移植** loop_core 的 executor/assertion_engine/transport（本仓 ws_acceptance 标签 DSL + 三级通道是更强的领域对应物）
- 不做跨 session 的失败知识库（YAGNI，emit 复盘已承担全局纠错）
- 不自动 push/commit（上传归 apply/人工）

## 12. 风险登记（保留项）

| 风险 | 缓解 |
|------|------|
| 正常路径收益薄（多一层透传） | 接受；分层价值在故障路径与纪律防退化 |
| 接口税（3 个新接口） | 字段全部批次现成；SKILL 给模板 |
| 迁移须一次改齐（漏改=双循环并存） | 实现计划单列"散文退役"验收项 |
| 指纹误判 | 归一化 + total 护栏 + 诊断报告人工复核路径 |
| 收据配额加速 | 可选 `_DETAIL_KEEP` 微调；接受窗口缩短 |
| 排障路径变长 | status 一站式 + 关联 ID grep |

## 13. 实现物清单

1. 新增 `harness/skills/loop-engineering/ws_session.py`（~200 行：状态机/指纹/计数/归因/报告骨架，无执行逻辑）
2. 新增 `harness/skills/loop-engineering/SKILL.md`（职责/双入口/CLI 契约/AI 职责）
3. 新增 `harness/skills/loop-engineering/tests/test_ws_session.py`（状态机/指纹归一化/双层计数/归因/降级/会话老化/模式 B 独立闭环，全离线不依赖设备）
4. 修订 `harness/skills/cross-device/cross-device-apply/SKILL.md`（步骤 5 改拉起 loop + 四字段拆解模板 + 收尾协议）
5. 修订 `harness/skills/workspace-verify/SKILL.md`（删除 L36 自愈散文，标注"跨轮循环归 loop-engineering"）
6. 验收：全仓 pytest 通过；模拟一次模式 B session（离线脚本测试）验证状态机全路径

## 14. 验收标准

- [ ] 模式 A：-sv 批次经 loop 执行，pass 批收据数=1，行为与基线等价
- [ ] 模式 B：`start --target dev --case lcview-liveness` 可跑通会话闭环
- [ ] 模式 B 独立性：不引用 apply 任何组件可完成完整会话（离线测试断言）
- [ ] 会话老化：超 20 份时删最旧已终结目录，活跃目录不删（离线测试断言）
- [ ] 同一问题 3 次冻结退出，诊断首行归因 task_unsolvable
- [ ] 多阶段各失败 1 次不退出（patience 清零语义）
- [ ] framework_error 停环不重试
- [ ] session 删除后降级为基线行为
- [ ] 诊断内容出现在末轮收据 body（emit 可见）
- [ ] 两处散文（apply L29 保留但 scope 收窄 / verify L36 删除）语义无并存冲突

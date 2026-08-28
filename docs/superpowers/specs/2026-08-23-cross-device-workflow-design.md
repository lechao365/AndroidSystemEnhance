# 跨设备协同开发工作流（cross-device pack）设计 spec

- 日期：2026-08-23
- 状态：已批准（Approved）
- 范围：harness/skills 下的 cross-device pack 与 4 个独立 skill、CDP 契约、data/verify 收据机制、SRC 规则修订

## 1. 背景与目标

### 1.1 现状

- `~/workspace`（AOSP + 内核源码树）是唯一编译源码树；改动 workspace → 上板验证 → `/sync-workspace-to-code` 归档到 `code/rpi5/`，code 即可运行基线。
- dev 分支 NG（含变砖）时用 `/sync-code-to-workspace`（需 promoted baseline）把 workspace 拉回一致。
- SRC-001/002 规定：改动必须先改 workspace、`code/` 严禁手改。

### 1.2 目标流程（全面切换，源头反转）

1. **emit 设备（远端）**：用户在本仓 clone 上 `git pull`，用强 LLM（如 claude opus 5）分析仓内信息（diff、涉及文件全量、验证收据、docs），给出修改方案，经 `cross-device-emit` 生成批次 prompt（CDP 纯文本，可能多轮）。
2. **apply 设备（本地 WSL2）**：用户拷贝 CDP 文本，经 `cross-device-apply` 直接修改 `code/`（dev 分支）；若批次标记需上板验证则拉起 `workspace-verify`（code→workspace 同步→编译→增量推送→提取结果→写收据）；最后经 `git-works-push` 提交推送 dev（收据随批入库）。
3. **emit 侧迭代**：强模型基于最新收据与 codebase 分析，决定下一轮 prompt 或宣告任务结束；任务结束后由 `sync-modify-to-main-base` 把 dev promote 到 main 形成新基线（三段式证据链）。
4. **回退**：dev 持续 NG 且强模型也无法修复时，人工触发 `revert-modify-from-main-base` 回退 dev 到 main 并恢复开发板到基线状态。

### 1.3 设计原则

- **repo 即 pack**：契约文档、解析器、SKILL.md、command 薄入口全部在仓内单份存放；emit/apply 两侧均为 clone，pull 后天然同步，无需安装/分发机制。
- **不依赖外部 skill**：全部逻辑内聚 `harness/`，不引用用户目录 lc-skills 安装态（可参考其实现，但不形成运行时依赖）。
- **零确认全自动**（apply→verify→push 链路），高危操作（整卡刷写、boot 分区 dd、回退）人工确认。
- **apply 侧自愈优先 → emit 侧强模型兜底 → 人工决策兜底**。

## 2. 角色与数据流

```
┌─ emit 设备（远端）─────────────┐        ┌─ git server（唯一共享媒介）────────┐
│ repo clone + opencode          │ pull   │  dev 分支：code 改动 + data/verify  │
│ cross-device-emit              │◄──────►│  main 分支：基线                    │
│ 强 LLM 分析 → 生成 CDP 文本     │        └───────────┬───────────────────────┘
└──────────┬─────────────────────┘                    │ pull / push
           │ CDP 纯文本（人工拷贝）                    │
           ▼                                           ▼
┌─ apply 设备（本地 WSL2）──────────────────────────────────┐
│ cross-device-apply：改 code/dev → (-sv 时)拉起             │
│   workspace-verify：code→workspace→编译→增量推送→收据      │
│ git-works-push：commit+push dev（收据随批入库）            │
│ sync-modify-to-main-base / revert-modify-from-main-base   │
│ 开发板（网络 adb / 串口）                                   │
└────────────────────────────────────────────────────────────┘
```

- **emit 设备**：禁止直接改 code/（流程违规，apply precheck 拒收）。
- **apply 设备**：本仓 clone（dev）+ `~/workspace`（编译缓存）+ 开发板（网络 adb / 串口）。
- **git server**：dev=迭代分支，main=基线分支；`data/verify/` 收据随批入库，是两侧唯一状态交换媒介。

## 3. 目录与 command 布局

```
harness/skills/
├── cross-device/                          # pack（emit/apply 共用契约与解析器）
│   ├── docs/cdp-contract.md               # CDP 契约 SSOT（与解析器成对维护，CDP-001 纪律）
│   ├── lib/python/cdp_parse.py            # 契约解析/校验（emit/apply 共用，仓内单份）
│   ├── cross-device-emit/SKILL.md
│   ├── cross-device-apply/SKILL.md
│   └── tests/                             # 解析器 pytest
├── workspace-verify/                      # SKILL.md + verify 脚本（含 adb 连接子模块）
├── git-works-push/                        # SKILL.md + collect_diff/commit_push 脚本（自包含）
├── sync-modify-to-main-base/              # SKILL.md + 脚本（移植 branch-merge + baseline 闭环）
└── revert-modify-from-main-base/          # SKILL.md + 脚本（新实现）

.opencode/command/                         # 6 个薄入口（@引用 SKILL.md），随仓分发
├── cross-device-emit.md
├── cross-device-apply.md
├── workspace-verify.md
├── git-works-push.md
├── sync-modify-to-main-base.md
└── revert-modify-from-main-base.md

data/verify/                               # 仓内收据（详见 §5）
```

要点：

- command 薄入口机制沿用现有 sync-* 模式（`.opencode/command/<name>.md` 由用户显式触发，`@` 引用 SKILL.md）。
- emit 侧的 command 在 apply 设备同样可见；SKILL.md 中注明 apply 类 command 仅限 apply 设备运行。
- 运行日志落 `harness/log/<skill-name>/`（gitignore，不进仓）。

### 3.1 旧 sync skill 处置

| 旧 skill | 处置 | 原因 |
|---|---|---|
| sync-workspace-to-code | deprecated（SKILL.md 头部声明 + command 标注弃用，**不删除文件**） | 流程反转后 workspace→code 归档方向消亡 |
| sync-code-to-workspace | 保留交互模式（前置条件从 promoted baseline 放宽为 dev/main HEAD）+ 脚本核心被 workspace-verify 复用（新增 `--auto` 强制镜像模式：跳过 promoted 校验 → 自动计划全选 → apply → 落盘校验，plan 空视为成功） | code→workspace 场景仍存在（人工恢复 + verify 内部步骤） |

回退路径同样单向：revert 后也是 code→workspace 同步，全流程不存在 workspace→code 写入。

## 4. CDP 契约（cdp-contract.md）

### 4.1 格式

```
-sv base:1a2b3c4d5e6f
意图: <这一轮要达成什么>
验收: <可执行判据，至少一条语法化条目；-s 批次填「无」>
方向: <实施方向/技术要点/约束>
```

### 4.2 规则

- **首行模式标记**：
  - `-s`：仅代码改动，无需上板验证；
  - `-sv`：需上板验证。
- **`base:` 必填**：跟在首行 = emit 产批时 pull 后的 `origin/dev` HEAD 前 12 位 hex（由 emit precheck 自动获取，不手工计算）；apply 侧起始 dev HEAD 前 12 位与之不匹配则整批拒绝（exit 3），防错位应用。
- **三标签（意图/验收/方向）必填**，各占一段；`-s` 批次验收必须填「无」保持结构统一。
- **字符预算**：总字符 50~500（含首行）；复杂任务由 emit 侧强模型拆多轮，每轮基于上一轮推送后的新 HEAD 重新产批（base 链式递进）。
- **batch_id**：规范化文本（剥 BOM/逐行 strip/去空行/LF 统一）sha256 前 12 位，跨设备一致可对账。
- **验收标签（推荐格式，非强制）**：`-sv` 批次验收必须非空且不得为「无」；内容建议使用以下语法化条目（供 verify 脚本自动执行），也允许自由文本由 verify 侧 AI 现场判断：
  - `svc:<service>` —— 服务 running
  - `log:<keyword>` —— logcat 命中关键字
  - `prop:<k>=<v>` —— 属性值匹配
  - `file:<remote_path>` —— 设备上文件存在
  - `cmd:<shell>` —— shell 命令 exit 0
  - `boot` —— 设备存活且 `sys.boot_completed=1`

### 4.3 解析器退出码

| 码 | 含义 |
|---|---|
| 0 | 通过 |
| 3 | 参数/批次文件错误 |
| 11 | 结构错误（首行模式标记/标签格式） |
| 12 | 空批次 |
| 14 | 三标签缺失 |
| 15 | base 非 12 位 hex |
| 16 | 预算超限（>500 或 <50） |
| 17 | 验收规则违规：`-sv` 验收为空或为「无」/ `-s` 验收非「无」（emit 角色严格；apply 角色降级 WARN） |

契约文档与解析器成对修改（继承 CDP-001 纪律：禁止单独改一边）。

## 5. data/verify 收据

```
data/verify/<YYYYMMDD-HHMMSS>-<batch_id>.md   # 详情（保留 50 份，老化淘汰）
data/verify/trend.md                          # 单行趋势（保留 50 行）
```

### 5.1 详情收据 schema（schema_version: 1，markdown key-value 头）

| 字段 | 说明 |
|---|---|
| schema_version | 1 |
| batch_id | 规范化批次文本 sha256 前 12 位；独立触发模式为 `manual-<10位时间戳>`（%y%m%d%H%M），revert 恢复验证为 `revert-<10位时间戳>` |
| batch_base | 批次 base commit（独立模式为验证目标 commit） |
| verified_commit | 验证/编辑起点 HEAD（= 该批 commit 的 parent；独立模式 = 验证目标 commit） |
| verify_mode | board（-sv）/ none（-s） |
| result | pass / fail / skip / revert（revert 操作专用） |
| build | 编译阶段结果（pass/fail/skip + 耗时） |
| push_board | 上板推送结果（pass/fail/skip） |
| acceptance | 验收条目逐项结果 |
| elapsed_s | 总耗时 |
| summary | 一句话结论（取意图首句；独立模式由 AI 概括） |

正文包含：CDP 原文、各阶段明细、失败现场摘录（logcat/dmesg 片段，经脱敏处理）。

### 5.2 其他规则

- `-s` 批次同样写收据：`result:skip`、三阶段全 skip（保证每批有迹可循；emit 侧 precheck 依赖它判定上批已推送）。
- trend 行格式：`<YYYY-MM-DD HH:MM:SS> <batch_id> <result> build=<x> board=<x> acc=<x> <summary>`。
- 收据只落盘不 commit，由 git-works-push 随本轮代码改动统一提交推送。
- revert 操作写两份收据：revert 收据（记录被丢弃的 commit 区间）+ 恢复验证收据（走独立模式，batch_id=`revert-<时间戳>`），供 emit 侧追溯。
- **emit 侧「上批已推送」判定**：读 trend 末行 batch_id → 详情取 `verified_commit` → `git merge-base --is-ancestor <verified_commit> origin/dev` 且 `origin/dev HEAD != verified_commit`（存在后续 commit 即该批已推送）。

## 6. Skill 规格

### 6.1 cross-device-emit（emit 设备运行）

工作流：

1. **precheck**：`git pull`；工作树干净；本地 HEAD == origin/dev；上一批收据已推送（读 `data/verify/trend.md` 末行 batch_id → 详情取 verified_commit → `git merge-base --is-ancestor <verified_commit> origin/dev` 且 `origin/dev HEAD != verified_commit`）。上一批未推送则拒绝产新批。
2. **上下文组装**（指引强 LLM）：`git diff main..dev` 全量（每轮都从基线看当前 dev 状态；聚焦时可看上一批 `batch_base..dev` 增量）+ 涉及文件在 code/ 下的全量内容 + 最新 verify 收据（失败现场）+ 相关 docs/ 章节。
3. **产批**：`-s`/`-sv` + base + 三标签，总字符 50~500；**base 自动取 precheck 后的 origin/dev HEAD 前 12 位**（不手工计算）；复杂任务拆多轮。
4. **selfcheck**：`python3 harness/skills/cross-device/lib/python/cdp_parse.py --role emit <批次文件>` 必须 exit 0。
5. **输出**：纯文本批次（无包裹标记），交用户拷贝到 apply 设备。产一批等一批，不并行产下一条。

约束：emit 侧禁止直接 git commit/push、禁止改 code/。

### 6.2 cross-device-apply（本地运行）

工作流：

1. **precheck（精简）**：仅 repo 存在 / dev 分支 / 工作树干净 / base 匹配四项。去掉 lc-skills 的 install_state、prev_batch_report、CI runner 检查。
2. **应用编辑**：AI 按 CDP 意图/方向直接编辑 `code/` 全目录（含 `code/rpi-zero2w/`，其组织格式与 rpi5 不同，暂无对应 prompt 但编辑范围放开）。**编辑载体规则**：
   - `new/` 文件、`rpi-zero2w/`、`others/` 下文件：全量文件，直接编辑；
   - `modified/*.diff`：只能在 diff 的 hunk 内编辑（改 `+` 行与已有 context 行），**禁止引入 diff 外的新 context**（防 `git apply` 上下文失配）；编辑后跑 diff 格式校验器（新增 `cdp_validate_patch.py`：校验 `diff --git` 头 / `index` / `---`/`+++` / hunk `@@` 格式 / 行前缀合法性）。
   - 涉及 `code/rpi5/` 改动时自动重生成 `code/rpi5/manifest.yaml`：抽取 `sync_workspace_to_code.py` 的 `generate_manifest()` 为独立可调模块（或原脚本加 `--gen-manifest-only` 入口），deletions 传空列表（deletions 段仅旧流程 workspace 删除历史，新流程删除语义 = 删除 code 侧 .diff/new 文件，verify 同步时按 EXTRA 逻辑回退 upstream）。
3. **分流**：
   - `-sv` → 拉起 workspace-verify（**验证失败也继续走 push**——失败收据正是 emit 侧分析所需）；
   - `-s` → 跳过验证，写 skip 收据。
4. **推送**：拉起 git-works-push。
5. **自愈**：编辑环节失败时本地修复重试，每环节上限 3 次，超限继续走收据 fail → push。
6. 运行日志落 `harness/log/cross-device-apply/`。

### 6.3 workspace-verify（本地运行）

**两种输入模式**：
- 模式 A（apply 拉起）：`--batch-file <cdp 文件>`，batch_id 从文本派生、验收从批次解析；
- 模式 B（独立触发）：`--target <dev|main|commit> --acceptance "<标签>"`，batch_id=`manual-<时间戳>`，验收默认基础检查（boot + 指定标签）——用于单独重跑验证、revert 后恢复验证。

步骤：

1. **code→workspace 强制镜像**：复用 sync_code_to_workspace.py 核心，新增 `--auto` 模式（跳过 promoted baseline 校验，改为校验 dev 或 main HEAD 存在 → 自动生成计划 → 全选 `+` → apply → 落盘校验；plan 为空视为成功 exit 0）。同步源 = **code 工作树当前状态**（含 apply 未提交改动）；**同步范围限定 `code/rpi5/{aosp,kernel}`**——`data/verify/`、`others/`、`rpi-zero2w/` 不参与同步（旧脚本扫 aosp/kernel 目录树的既有行为）。
2. **影响面判定**：按改动路径分类（aosp 模块 / 内核 / boot 相关）。
3. **编译**：lcview 相关改动先跑单测门禁 `make lechao_lcview_unit_test lechao_lcview_hal_test -j$(nproc)`；再按 incremental-dev-reference 选增量路径（`m <module>` / `make bootimage ...` / `mk_rpi5_full_image.sh -mode 2|3|4`），严格遵守 BLD-001~012 / INC-001~010 规则（含 BLD-001~003 内核工具链与产物、BLD-004 lunch 前 source envsetup、BLD-005 禁裸 make、BLD-007 sudo 打包显式传参、BLD-009 CCACHE_DIR=out/ccache、INC-001 禁 make clean/clobber、INC-006 内核 Image+dtbs 同源、INC-007 VINTF 绕过、INC-009 android_rpi5_defconfig）。
4. **adb 增量推送**：自包含连接层——mDNS 发现（`adb mdns services` 匹配 `_adb(-tls-connect)?._tcp`）→ 静态 fallback（默认 `rp5.local:5555`，环境变量可覆盖）；`adb root` + `adb remount` + push 产物 + 重启服务或 reboot（遵守 INC-003/004/005）。
5. **验收执行**：语法化条目（svc/log/prop/file/cmd/boot）由脚本自动逐项执行；自由文本判据由 verify 侧 AI 现场 logcat/dmesg 判定。
6. **收据落盘**：写 `data/verify/`（脱敏、含失败现场摘录）。

例外与故障：

- **人工确认门**：整卡刷写、boot 分区 dd。
- **设备不可达**：串口诊断砖机三分法（adb 不可达 + 串口静默 = 断电全砖；串口有启动日志 = 半砖；反复相同日志 = boot loop），收据 fail + 建议。
- **编译/验收失败自愈**：verify 侧 AI 读错误日志 → 修 code → 重跑该环节，上限 3 次；超限收据 fail（含现场）。

### 6.4 git-works-push（本地运行）

- 保留：diff 收集（staged/unstaged/untracked，大 diff 降级阈值 50 文件/5000 行）→ AI 中文 commit message（沿用中文 type 格式：新增/修复/重构/文档/杂项/构建）→ add/commit/push origin dev；永不推 main 守卫；`--push-only` 模式；push 失败 commit 保留（exit 2）。
- 去掉：dev 自动创建、amend、message 三重校验等冗余前置。
- push 失败不自动重试，报告后转用户手动处理。
- 脚本自包含（所需 shell 函数内联进项目，不依赖 lc-skills lib）。

### 6.5 sync-modify-to-main-base（本地运行）

移植 lc-skills-git-works-branch-merge 的 prepare → squash promote → 一致性校验 → push main → 重建 dev 流程，新增 baseline 三段式闭环：

1. **prepare（含前置校验）**：校验最新 verify 收据 `result ∈ {pass, skip}`（`-s` 批次 emit 已判定无需上板验证，视为 OK）**且** `HEAD^ == verified_commit`（dev 无未验证改动）；通过后自动登记 baseline-status.yaml **candidate**：`baseline_id: BL-YYYYMMDD-NN`、`source_branch: dev`、`source_commit: <dev HEAD>`、`sync_manifest: <最新 data/verify 收据路径>`（字段复用为收据路径，同步更新 baseline-evidence-template.yaml 注释）、`build_result/package_result/board_verify: PASS` + 附收据路径。新流程登记从 candidate 起步（archive 仅旧流程历史）。
2. **人工评审**：用户检查 candidate。
3. **promote**：`--promote` 执行 squash 到 main → 一致性校验（`git diff --quiet main dev`）→ push main → 重建 dev（reset --hard main + 删远端 dev 重推）→ candidate 升级 **promoted**（approved_by/approved_at）。promote 成功后自动拉起 `/sync-code-to-doc` 文档同步。

### 6.6 revert-modify-from-main-base（本地运行，人工触发）

1. **人工确认门**：列出将丢弃的 `main..dev` commit 清单，用户显式确认。
2. 执行：dev `reset --hard origin/main` + force push origin dev。
3. 写 revert 收据（记录被丢弃区间各 commit）。
4. code→workspace 同步回 main 状态（`--auto` 模式）。
5. 对 main HEAD 自动跑一次 workspace-verify（**模式 B 独立触发**，batch_id=`revert-<时间戳>`），确保开发板恢复正常基线。

## 7. 规则与文档修订

| 文件 | 修订内容 |
|---|---|
| `harness/rules/source-code-modify.md` | 全面改写：SRC-001/002 重写（code/dev 分支是唯一改动源头，workspace 是编译缓存；手工调试允许临时改 workspace 试验，但必须回填到 code 才能进入验证/推送链路）；保留 blockquote 条目格式；正文的改动规则表、禁止行为节、归档纪律表同步改写（仅改条目会导致文件自相矛盾）；SRC-003（others 独立维护）保留；SRC-004 更新为「仅 promoted（main）可作为恢复真相源」并保留证据字段按 baseline-evidence-template.yaml 填写登记的措辞 |
| `AGENTS.md` | Harness 命令表增 6 项；sync-workspace-to-code 标注 deprecated（command 与 SKILL.md 头部声明，**不删除文件**）；Baseline 指引更新（candidate 由 verify 收据自动登记） |
| `harness/README.md` | 工作流说明同步更新 |
| `harness/config/baseline-evidence-template.yaml` | 注释更新：`sync_manifest` 字段在新流程中复用为 data/verify 收据路径 |

## 8. 错误处理模型

**apply 侧自愈优先（每环节上限 3 次）→ emit 侧强模型兜底（多轮 prompt 修复）→ 人工决策兜底。**

| 故障点 | 行为 | 恢复路径 |
|---|---|---|
| base 不匹配 | apply 拒批（exit 3） | emit 侧重新产批 |
| 编辑某文件失败 | apply 自愈修复；diff 编辑后格式校验器/verify 同步 `git apply --check` 失败即报错；超限标 FAILED 继续，收据记录 | emit 侧下轮修复 |
| code→workspace 同步失败 | verify 中止，收据 fail | 重跑 verify |
| 编译失败 | verify 自愈；超限收据 fail + 错误日志摘录 | emit 分析下轮修 |
| adb 不可达 | 串口诊断砖机三分法，收据 fail + 建议 | 人工决策（正向修复 / revert） |
| 验收失败 | verify 自愈；超限收据 fail + logcat/dmesg 现场 | emit 分析下轮修 |
| push 失败（网络/非快进/认证） | commit 保留（exit 2），不自动重试 | 转用户手动处理 |
| dev 持续 NG | emit 侧强模型多轮尝试 | 强模型也无法修复时，用户手动触发 revert（永不自动） |
| emit 侧直接改 code | 流程纪律违规（emit 侧 no_commit 约束；apply 侧无技术检测手段，依赖纪律与人工评审） | 评审发现即回退 |

## 9. 测试策略

- **解析器单测**：cdp_parse.py 全错误分支（结构/base hex/预算/验收规则）**python3 自带 unittest** 用例（项目无 pytest 基础设施，保持零依赖），位于 `harness/skills/cross-device/tests/`，运行 `python3 -m unittest discover -s harness/skills/cross-device/tests`。
- **diff 格式校验器单测**：cdp_validate_patch.py 合法/非法 diff 样本用例。
- **收据读写单测**：收据生成/解析/trend 老化 round-trip。
- **脚本干跑模式**：所有新脚本支持 `--check-only` / `--dry-run`（verify 同步计划、push diff 预览、promote prepare、revert 丢弃清单）。
- **端到端验收**：按实施计划分阶段，用一次真实小改动走通 emit→apply→verify→push→promote 全链。

## 10. 范围外（Out of Scope）

- rpi-zero2w 的 CDP 批次与验证流程（编辑范围已放开，verify 流程暂不覆盖其编译推送）。
- 整卡刷写 / boot 分区 dd 的自动化（永久人工确认）。
- emit 设备侧 skill 的自动分发机制（clone + pull 天然覆盖）。
- lc-skill-manager 式 pack/tests/validators 双形态与安装器。

---
name: loop-engineering
description: loop engineering v2 工作流（用例驱动 + AI 修复闭环）
---

# Loop Engineering v2 Workflow

## 目标

AI 接管设备验收：执行用例 → 输出证据 → AI 分析 → 修复代码 → 重测 → 循环直到全 pass。

## 核心流程（全自动闭环）

```
[Step 0] (可选) AI 读代码/spec + case-template.md → 生成 YAML 用例
         → le gen-cases --validate <file>   # 校验 schema/断言/命名/依赖

[Step 1] le control init --target <module> --max-attempts 5 --artifacts-dir <dir>

[Step 2] le control run-verify --session <sid> --suite <suite> [--adb-endpoint <ep>]
         → 执行用例 → evidence_bundle.json → 提取 failed_cases 写入 session

[Step 3] le control decide --session <sid>
         → PASS → STOP（成功）
         → RETRY → 继续 Step 4
         → STOP + escalate → 人工介入

[Step 4] (仅 RETRY) le control analyze-request --session <sid>
         → analysis_request.json（failed_cases + collectors_output）
         → 主会话 AI 读 evidence_bundle.json + analysis_request.json → 生成 patch.json

[Step 5] (仅 RETRY) le control apply-patch --session <sid> --patch <patch.json>
         → 白名单校验 → 语法检查 → git stash 备份 → apply

[Step 6] (仅 RETRY) le control compile --session <sid>
         → 成功 → 继续 Step 7
         → 失败 → le control revert --session <sid> → 计入 N → goto Step 3

[Step 7] (仅 RETRY) le control deploy --session <sid> [--adb-endpoint <ep>]
         → DeployDecider 决策（能 PUSH_SINGLE 则不 dd）
         → DD_BOOT_REBOOT: 四阶段防护网（镜像验证/健康检查/dd/panic检测）

[Step 8] goto Step 2，直到全 pass 或 escalate
```

### escalate 触发条件

- N > max_attempts（默认 5）
- 同 failure_code 连续重复（REPEATED_FAILURE）
- 补丁内容重复（patch_hash 相同）
- 白名单拒绝（PATCH_REJECTED）
- 编译失败（COMPILE_FAILED）
- boot_completed 超时 + serial 无 shell（kernel 死）

### 部署约束（硬规则）

1. **能 PUSH_SINGLE 不 dd**：只要改动可走推送（二进制/.cpp/脚本），绝不上 dd boot.img
2. **dd 前四阶段防护网**：
   - 阶段1：补丁白名单 + 内核改动风险标记
   - 阶段2：镜像完整性/大小校验 + 备份
   - 阶段3：设备健康基线 + 备份完整性 + 磁盘空间
   - 阶段4：boot_completed + panic marker + 关键 service 存活 + serial shell 可达
3. **kernel 死 escalate 人工**：serial 无 shell 时软件无法自救，需物理重刷 SD

## 架构拓扑

```
opencode (AI Driver)
    ↓ le run
LE 框架 (loop_core)
    ├── case_loader       YAML 用例加载（include/requires）
    ├── assertion_engine  确定性断言（6 种类型）
    ├── executor          用例执行 + collector 触发
    ├── runner            通用 LoopRunner（场景无关）
    └── evidence          EvidenceBundle JSON 输出
    ↓ transport
connection (rp5-serial provider)
```

## 分层职责

| 层 | 职责 |
|----|------|
| opencode (AI) | 生成用例 / 分析证据 / 收敛候选修复方向 |
| `engineering/loop/controller/` | loop session / terminate-retry-regression policy / workflow 调度 |
| `engineering/loop/workflows/` | loop 专属 phase plan / bootstrap / verify / fallback / rerun |
| `loop_core` | 单次 attempt：用例加载 / 断言 / 执行 / 证据输出 |
| `cases/*.yaml` | 场景定义（声明式，零 Python） |
| `connection` | 传输层（串口 / ADB） |
| `engineering/harness/` | 公共规则、路径管理、脚本 bootstrap、日志与 observability 基础设施 |

## 传输层依赖链（serial → adb）

RPi5 采用 DHCP 动态分配 IP，**不使用固定 IP**。因此网络 adb 连接必须遵循以下依赖链：

1. **串口是唯一可信的 IP 发现通道**：设备启动后，host 无法预先知道 wlan0 的 IP，
   只能通过串口执行 `ip -4 addr show wlan0` 或从串口缓冲中提取。
2. **串口取 IP 是 adb 连接的硬性前置**：任何 `transport=adb` 的 suite / `run_on: host`
   的 adb 动作，都必须在串口 bootstrap 成功后才能执行。
3. **IP 发现由 `rp5_serial_helper.py device-ip` 统一承接**：
   - writer 空闲时：主动向设备发 `ip addr` 命令并读回
   - writer 被占用时（le run 期间）：从 host 环形缓冲捞最近 400 行匹配
4. **endpoint 传递**：
   - case 内闭环：`run_on: host` 的 case 在 command 内联调用 helper 取 IP（见
     `cases/system/network-adbd-success.yaml` 的 `host_adb_connect_success`）
   - workflow 级闭环：`run_lcview_adb_suite.sh` 的 `discover-adb-endpoint` 阶段取 IP
     后通过 `--adb-endpoint` 传给 feature suite
5. **禁止硬编码设备 IP**：源码 / yaml / 脚本中不得出现固定的设备 IP 字面量作为
   fallback 或默认值。`le deploy` 缺失 `--adb-endpoint` 时报错退出。

> 标准前置 suite：`cases/system/network-adbd-success.yaml` 是"串口 bootstrap →
> adb connect 成功"的闭环验证，所有 feature adb suite 应在其 PASS 后执行。

## 规则复用模型

### FQN 命名

- case FQN = `<suite>.<id>`（如 `system.boot.zygote_running`）。
- collector FQN = `<suite>.<name>`（如 `common.shell.crash_dump`）。
- `requires` / `on_fail.collectors` 可写短名：loader 按本地命名空间 → 显式 FQN →
  全局唯一短名 三段式解析（见 `case_loader._resolve_case_links`）。

### 公共 suite 与诊断 collector 库

`cases/common/shell.yaml`（`common.shell`）提供：
- 原子用例 `shell_reachable`（作为系统用例的 `requires` 前置）。
- 公共诊断 collector：`boot_log` / `init_log` / `crash_dump` / `serial_recent`。
- `serial_recent` 为串口上下文 collector（`mode: serial_context`），无需 shell 可达
  即可获取 host transcript 路径、最近串口片段与重启周期，是 zygote 反复重启等场景的
  串口第一现场证据入口。

业务 suite 通过 `include: [common/shell]` 自动注入上述用例和 collector，
失败时直接用短名引用即可，无需重复定义。新场景应优先复用公共 collector，
仅在场景专属诊断（如 HAL / sensor 特定日志）时定义本地 collector。

### include 解析

- `include` 路径由 `--case-dirs` 解析；loader 在每个 case_dir 下找 `<name>.yaml`。
- 因此 `include: [common/shell]` 要求 `--case-dirs` 包含 `cases/` 根目录。

## core 模块清单

| 模块 | 职责 |
|------|------|
| `models.py` | ObservedLine / TestCaseResult / CollectorResult / EvidenceBundle |
| `assertion_engine.py` | 确定性断言（contains/regex/equals/prompt_visible/not_contains/exit_code_zero） |
| `case_loader.py` | YAML 加载 + include + requires 拓扑排序 |
| `executor.py` | 用例执行 + collector 触发（去重） |
| `collector.py` | 深度证据采集（含 `serial_context` 模式，消费 transport runtime context） |
| `runner.py` | 通用 LoopRunner（场景无关） |
| `evidence.py` | EvidenceBundle JSON 输出 |
| `host_exec.py` | host 执行平面（`run_on: host` 的命令执行） |
| `provider_loader.py` | provider 动态加载（按 transport 选择 provider） |
| `report.py` | evidence.py 薄封装 |
| `cli.py` | 统一 CLI（le run / gen-cases / deploy） |
| `config.py` | DeviceProfile（设备语义 + 默认执行参数） |
| `transport.py` | BaseTransport + FixtureTransport |
| `observer.py` | capture_snapshot（prompt 探测） |
| `cycles.py` | cycle 切分工具（可选） |

## 扩展新场景

1. 参照 `templates/case-template.md`
2. 在 `cases/system/` 下创建 `<scenario>.yaml`
3. `le.sh run --suite <path> ...`

无需写任何 Python 代码。

## 场景细节

### system.network_adbd

`cases/system/network-adbd-success.yaml` 用于验证 RPi5 的开机自动联网与网络 adb 闭环：

1. `trigger_reboot` + `shell_reachable`
2. `boot_completed`
3. `rpi5_wifi_connect` 服务已进入有效执行态
4. `/data/boot/wifi.conf` 存在且非默认值
5. 已连接目标 SSID
6. `wlan0` 获得 `192.168.1.x`（DHCP 分配，由串口动态发现）
7. adbd TCP 属性正确，`adbd` 为 `running`
8. host 通过 `rp5_serial_helper.py` 动态发现设备 IP 并 `adb connect` 成功

该场景继续以串口作为主执行与主取证通道；host adb 仅作为最终成功判据，而不是主 transport。

Live 运行示例：

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
python3 -m loop_core.cli run \
  --suite engineering/loop/cases/system/network-adbd-success.yaml \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir engineering/output/runs/network-adbd-live \
  --host 127.0.0.1 \
  --port 9700
```

运行前要求：

- host 环境可直接调用 `adb`
- 设备端 `wifi.conf` 已配置真实 `ssid/psk`（DHCP 模式，无 static_ip）
- 设备 IP 由串口 helper 动态发现，不硬编码

### system.adb_shell

`cases/system/adb-shell-success.yaml` 是 `transport=adb` 的最小 smoke suite：

1. `adb shell` 可达
2. `sys.boot_completed=1`
3. `init.svc.adbd=running`
4. `id` 命令可执行

建议在实现任何 feature adb suite 前先单独跑通本场景。

### features.lcview

`cases/features/lcview/common.yaml` 提供：

- adb shell reachability
- `sys.boot_completed`
- HAL / daemon service state
- schema / data dir readiness
- pull logs / invalid log / runtime context final collectors

### features.lciod

`cases/features/lciod/common.yaml` 提供：

- adb shell reachability + boot_completed + fault-verify 工具就绪
- HAL / Daemon 服务注册检查
- 设备节点 `/dev/vendor_lechao_usbd*` 存在性
- HAL / Daemon logcat / kmsg / fault-verify JSON / device_state 诊断 collector

`cases/features/lciod/kernel_driver.yaml` 覆盖 16 个内核驱动能力点（22 case）。

`cases/features/lciod/hal.yaml` 覆盖 8 个 HAL 能力点（10 case）。

`cases/features/lciod/daemon.yaml` 覆盖 8 个 Daemon 能力点（9 case）。

`cases/features/lciod/end_to_end.yaml` 覆盖 4 个端到端场景。

## 断言类型

| type | 用途 |
|------|------|
| `contains` | 输出包含文本 |
| `regex` | 输出匹配正则 |
| `equals` | 输出完全等于 |
| `prompt_visible` | shell prompt 可见 |
| `not_contains` | 输出不包含文本 |
| `exit_code_zero` | 退出码为 0 |
| `json_field` | 解析 JSON，按 path 取字段，op 比较（eq/ne/gt/ge/lt/le/exists/not_exists） |
| `exit_code_equals` | 退出码等于指定值 |
| `contains_any` | 输出包含列表中任一项 |

## `run_on` 执行平面

Loop case 与 collector 默认在 `device` 执行，即通过当前 transport（fixture / rp5-serial）向设备发送命令并采集输出。

当场景需要 host 侧动作（例如 `adb connect <ip>:5555`）时，可在 case 或 collector 上显式声明：

```yaml
- id: host_adb_connect_success
  run_on: host
  command: "DEV_IP=$(python3 rp5_serial_helper.py device-ip --host 127.0.0.1 --port 9700); adb connect $DEV_IP:5555"
  assert:
    type: regex
    pattern: "(connected to|already connected to)"
```

约束：

- `run_on` 只允许 `device` / `host`
- `action: reboot` 仅允许 `run_on: device`
- `prompt_visible` 与 `serial_context` 仅适用于 `device`

## EvidenceBundle 串口上下文

`evidence_bundle.json` 包含 `serial_context` 字段，承载串口第一现场证据：

| 字段 | 说明 |
|------|------|
| `transcript_path` | host 持续落盘的串口 transcript 文件路径 |
| `serial_snippet` | 最近 N 行（≤40）串口关键片段 |
| `reboot_cycles` | 基于 `reboot_markers` 估算的最近重启周期数 |
| `recent_line_count` | host 当前环形缓冲中的行数 |

`summary.txt` 同步渲染上述内容，方便人工快速浏览。

rp5-serial host 持续将串口正文写入 `transcript_path`（默认 `output/host-log/rp5-serial-transcript.log`），
每行带 ISO 时间戳。`serial_recent` collector 通过 `mode: serial_context` 直接消费 host 上下文，
无需 shell 可达即可获取串口根证据（transcript 路径 + 最近片段 + restart 周期）。

shell 不可达时，AI/人工应优先分析 `serial_context`；shell 可达时再结合 `init_log` / `crash_dump` 等 collector 证据。

## 遗留点

1. **gen-cases 已实现（校验器）**：`le gen-cases --validate <file>` 复用 load_suite 做 schema/断言/命名/依赖校验
2. **deploy 已实现**：`le deploy` + `le control deploy` 支持 push_single/dd_boot_reboot + 四阶段防护网
3. **loop_ctrl 已实现**：`le control {init,run-verify,analyze-request,apply-patch,compile,revert,deploy,decide,status}` 全链路全自动闭环
4. **FLASH_FULL 需人工刷机**：sepolicy/.te 大改动仍需人工物理重刷（serial 无 shell 时软件无法自救）
5. **参数化用例**：case_loader 预留 parameters 字段，当前未实现展开

> loop 控制面落地于 `engineering/loop/controller/`，不进入 `engineering/harness/`。

## AI 诊断报告约束（`/le` 第 4-5 步首版）

诊断报告只输出"确定事实 / 现象归类 / 当前不确定点 / 候选修复方向"，不强行给唯一根因。

当 AI（opencode）通过 `/le` 触发诊断闭环并收到 EvidenceBundle 后，必须遵守以下规则：

1. 任何 FAIL 都进入诊断阶段
2. 诊断阶段只读取本次 run 的 `summary.txt`、`evidence_bundle.json`、bundle 引用的 artifacts，以及 `serial_context`
3. 诊断前可选询问一次调查线索（最近改动模块、suspect 范围、首次坏版本等）
4. 调查线索（用户提供，未验证）必须标记为"用户提供，未验证"，不得覆盖客观证据
5. 报告文件固定写到与本次 `evidence_bundle.json` 同目录的 `diagnosis-report.md`
6. 报告必须包含 7 节：结论 / 证据链 / 现象归类与不确定性 / 调查线索 / 候选修复方向 / 建议新增调整 case / 循环终止建议
7. 不强行给唯一根因；允许并列多个候选修复方向
8. 只有当证据足以落到 `~/workspace/` 可操作范围时，才输出候选补丁草案；否则只出诊断报告
9. AI 不自动修改 `boot-success.yaml`
10. 诊断阶段可通过串口直接采集的设备信息（如 `/dev/dri/`、`/sys/class/drm/`、`getprop`、`dumpsys` 等），无需向用户逐条确认，直接执行

reboot 诊断闭环的数据流：
- `/le run --suite boot-success.yaml --host <ip> --port 9700 ...`
- executor 遇到 `action: reboot` case → 调 transport.reboot_and_wait
- reboot_and_wait 三级渐进判定（L1 boot 开始 / L2 init 阶段 / L3 boot_completed 验证）
- 后续 case（requires: [trigger_reboot]）在设备回来后正常执行
- on_fail 触发 collectors（含新增 kmsg）
## `le deploy` 子命令

部署模式由 git diff 内容自动决策：

- `push_single`：mmm 单模块编译 → adb remount → push binary → restart service（秒级生效，无 reboot）
- `dd_boot_reboot`：mk_rpi5_full_image.sh -mode 2 → push boot.img → dd + reboot（内核/init.rc 改动）
- `flash_full`：需要人工全量刷机（sepolicy/.te 改动，vendor dd 未验证）

用法：
```bash
le deploy --decide --diff-rev HEAD           # dry-run 查看决策
le deploy --diff-rev HEAD --adb-endpoint ... # 执行部署
```

## `le control` 子命令（全自动闭环）

```bash
le control init --target lciod --max-attempts 5 --artifacts-dir <dir>
le control run-verify --session <id> --suite <path> [--adb-endpoint <ep>]
le control decide --session <id>
le control analyze-request --session <id>
le control apply-patch --session <id> --patch <patch.json>
le control compile --session <id>
le control revert --session <id>
le control deploy --session <id> [--adb-endpoint <ep>]
le control status --session <id>
```

全自动闭环 SOP：init → run-verify → decide → (RETRY) analyze-request → apply-patch → compile → deploy → goto run-verify

护栏：
- apply-patch：白名单（target-paths.yaml）+ 语法检查 + git stash 备份
- compile：失败 → revert → 计入 N
- decide：N=5 / 同 failure_code 重复 / patch_hash 重复 → STOP escalate
- deploy：能 PUSH_SINGLE 不 dd；dd 前后四阶段防护网

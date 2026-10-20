# harness 特权操作 allowlist 与 sudo 绕过机制设计

- 日期：2026-10-20
- 状态：已实现（Task 1~7，2026-10-20）
- 范围：新增通用 `privileged_exec` 入口与仓库内 allowlist，统一处理 opencode 会话 `NoNewPrivileges=1` 下的 host sudo 需求；首期启用 RPi5 打包，刷机/块设备写以默认禁用的模板入库。仅改 harness 机制层与文档，不改 `code/` 业务代码。

## 1. 背景与现状

### 1.1 当前根因

opencode 以 systemd user unit 运行，unit 设置了 `NoNewPrivileges=true`
（`harness/skills/cross-device/opencode-server/start-opencode-server.sh:297`）。
该标志被子进程继承且不可撤销，会话内直接 `sudo`（含 `sudo -n true`）会被内核
拒绝，与 sudoers 是否 NOPASSWD 无关。

本批前的实际绕过方式是手动启动 transient service（现已由 allowlist 自动封装）：

```bash
systemd-run --user --property=NoNewPrivileges=no --wait --pipe --collect \
  /usr/bin/python3 harness/skills/workspace-verify/ws_package.py --mode 0
```

`systemd-run --user` 由 user manager 派生新进程，不继承 opencode unit 的
`NoNewPrivileges`；在免密 sudo 环境下，service 内的 `sudo` 可用。该方式已验证
可用，但目前是人工命令，不是 harness 的通用机制。

### 1.2 harness 内 host 特权点盘点

| 位置 | 现状 | 是否自动执行 | 处理 |
|------|------|--------------|------|
| `harness/scripts/mk_rpi5_full_image.sh:467` | `sudo rpi5-mkimg.sh`（分区/格式化/losetup） | 是 | 纳入 allowlist，核心启用 |
| `harness/skills/workspace-verify/ws_package.py:165` | 父进程 `sudo -n true` 探测 | 是 | 改为统一 helper 探测 |
| `harness/skills/workspace-verify/ws_package.py:184` | 调打包脚本 | 是 | 改为统一 helper 执行 |
| `harness/skills/workspace-verify/ws_verify_chain.py:345` | ad-hoc `systemd-run --user` 包装 | 是 | 删除，避免双层包装 |
| `harness/skills/publish-main-base/baseline_register.py` | 只读 package 证据/告警 | 否 | 更新提示，不直接 sudo |
| `harness/skills/publish-main-base/publish_main_base.sh` | 调 verify 链/prepare/promote | 否 | 不直接 sudo |
| `harness/skills/cross-device/opencode-server/start-opencode-server.sh` | 设置 `NoNewPrivileges=true` | 否 | 限制来源，保持不动 |
| `harness/reference/incremental-dev-reference.md:99,101` | 手工 `sudo dd boot.img` / `eject` | 否 | 破坏性模板，默认禁用 |
| `harness/reference/build-reference.md` / `env-setup-reference.md` | apt/swapfile 等环境初始化 | 否 | 文档化，不纳入自动化 |
| `harness/reference/debug-tools-reference.md` | minicom/modprobe | 否 | 文档化，不纳入自动化 |
| `lciod_check.py` / `lcview_check.py` / `lcview_recover.sh` | 设备侧 `adb root` + `adb shell dd` | 是 | 不走 host sudo，不需要本机制 |

结论：当前唯一会阻塞正常 harness 任务的 host sudo 路径是 RPi5 打包；`ws_verify_chain`
已有临时包装，但直接调用 `ws_package.py` 时缺少统一绕过，导致基线发布被卡住。

### 1.3 设计目标

- 默认拒绝：未在 allowlist 中显式登记的命令不能借道提权。
- 自动执行：命中 allowlist 的普通任务不弹确认、不等待密码、不要求退出会话。
- 可审计：每次特权调用落结构化审计记录，收据/打包证据可追溯。
- 可扩展：刷机、块设备写等破坏性操作以模板入库，启用时仍需人工确认。
- 失败关闭：机制不可用、参数不符、条目禁用时明确报错，绝不降级为任意 sudo。
- 保持限制：不修改 opencode service 的 `NoNewPrivileges=true`；只对 allowlist 条目创建 transient unit。

### 1.4 非目标

- 不自动修改 `/etc/sudoers`。
- 不为 apt/minicom/swapfile 等一次性环境初始化命令提供自动提权。
- 不用 shell 字符串执行特权命令；不接受任意 `sudo <cmd>` 透传。
- 不改变设备侧 `LCVIEW_ALLOW_DESTRUCTIVE_WRITE` / `adb root` 语义。
- 不把破坏性刷机/块设备写默认启用。

## 2. 信任模型

采用“仓库内静态 allowlist + 代码评审 + 自动 transient unit + 审计”模型：

1. allowlist 文件随仓库入库，条目变更走正常代码评审。
2. 默认 `deny`；只有 `enabled: true` 且命令、参数、路径、超时全部匹配才执行。
3. allowlist 是唯一的提权信任边界；LLM/脚本不能通过拼接命令绕过条目。
4. 执行上下文是当前用户的 transient service，root 仅来自目标脚本自身的
   `sudo -n`（或条目显式声明的 `as_root: true`）。
5. 破坏性条目默认 `enabled: false`，启用需同时满足条目启用、环境授权和人工确认。
6. 每次调用写审计；审计失败不阻断业务，但要在输出中告警。

威胁模型边界：本机制防“LLM/脚本误拼命令、误用 sudo、绕过 allowlist”，不防
“已拥有本机 shell 的用户”——后者本来就能自行启动 `systemd-run`；allowlist
的职责是把 harness 的提权面收窄到可评审条目，而不是做本机多用户隔离。

## 3. 组件设计

### 3.1 `harness/lib/privileged_exec.py`（新增）

统一入口，提供 API 与 CLI。

**API**

```python
probe() -> ProbeResult
# ProbeResult: sudo_n, no_new_privs, systemd_run, mechanism, usable,
#              detail, context_sudo_ok

run(entry_id, argv, *, reason="", timeout=None, cwd=None,
    env=None, confirm_destructive=False) -> RunResult
# RunResult: rc, entry_id, mechanism, sudo_n, no_new_privs,
#            context_sudo_ok, dur_s, audit_path, output_tail, error
```

**CLI**

```bash
python3 harness/lib/privileged_exec.py --list
python3 harness/lib/privileged_exec.py --check
python3 harness/lib/privileged_exec.py --entry-id package.rpi5.mode0 \
  --reason "baseline package" -- bash harness/scripts/mk_rpi5_full_image.sh -mode 0
```

- `--check` 输出机制能力 JSON：当前 `NoNewPrivs`、父进程 sudo 是否可用、
  `systemd-run` 是否可用、transient service 内 sudo 是否可用；不执行目标命令。
  机制不可用时退出码为 3。
- `--list` 输出条目 id/命令/启用状态/是否破坏性。
- 退出码：0 成功；1 命令失败；2 参数/allowlist 错误；3 机制不可用（失败关闭）。

### 3.2 allowlist 文件

allowlist 固定为仓库内 canonical 文件 `harness/config/privileged-allowlist.yaml`，
不提供 `LC_PRIVILEGE_ALLOWLIST` 运行时路径覆盖或测试模式；测试通过临时仓库/
mock 完成。`harness/config/privileged-allowlist.yaml`：

```yaml
version: 1
default: deny
entries:
  - id: package.rpi5.mode0
    description: RPi5 基线打包（mk_rpi5_full_image.sh mode 0）
    command: harness/scripts/mk_rpi5_full_image.sh
    interpreter: bash
    args: ["-mode", "0"]
    requires_privilege: true
    as_root: false
    mechanism: auto
    timeout_s: 900
    env_passthrough: [LANG, AOSP_WS, AOSP_ROOT, ANDROID_PRODUCT_OUT,
                      TARGET_PRODUCT, CDP_BATCH_ID, CDP_RUN_ID]
    trusted_roots:
      AOSP_WS: /home/lechao/workspace/aosp
      AOSP_ROOT: /home/lechao/workspace/aosp
      ANDROID_PRODUCT_OUT: /home/lechao/workspace/aosp/out/target/product/rpi5
    reason_required: true
    enabled: true
    destructive: false
    approved_by: release-review
    approved_at: "2026-10-20"

  - id: flash.rpi5.boot_dd
    description: 刷写 RPi5 boot 分区（破坏性，默认禁用）
    command: harness/scripts/flash_rpi5_boot.sh
    interpreter: bash
    args: ["--confirm"]
    requires_privilege: true
    as_root: true
    mechanism: auto
    timeout_s: 900
    env_passthrough: [LANG, ANDROID_PRODUCT_OUT,
                      LC_FLASH_DEVICE, LC_FLASH_IMAGE,
                      LC_PRIVILEGE_ALLOW_DESTRUCTIVE]
    trusted_roots:
      ANDROID_PRODUCT_OUT: /home/lechao/workspace/aosp/out/target/product/rpi5
    reason_required: true
    enabled: false
    destructive: true
    human_confirm: true
    approved_by: ""
    approved_at: ""
```

破坏性条目的设备/镜像参数不放进 allowlist 的固定 argv（避免为每个设备复制条目），
由 wrapper 从白名单环境变量 `LC_FLASH_DEVICE`/`LC_FLASH_IMAGE` 读取并做严格校验；
allowlist 只放固定 `--confirm`。首期只允许 `/dev/sdXn` 分区且 `removable=1`，
整卡 `/dev/sdX` 暂不纳入。

环境构造规则：

- `PATH`/`HOME`/`USER` 由 helper 使用可信值，不采用调用方值；即使列入
  `env_passthrough` 也会被忽略并覆盖。`HOME`/`USER` 经 passwd 解析，
  `PATH` 固定安全路径。
- `PYTHONNOUSERSITE=1` 强制注入；`LANG` 按需透传。
- `env_passthrough` 只允许白名单键；`LD_*`/`DYLD_*`/`PYTHON*`/`MALLOC_*` 前缀
  及 `BASH_ENV`/`ENV`/`SHELLOPTS` 等危险变量由 denylist 拒绝。
- 路径类变量必须同时登记在 `trusted_roots`：`AOSP_WS`/`AOSP_ROOT`/`KERNEL_WS`
  须与登记值完全一致，`ANDROID_PRODUCT_OUT` 须位于登记根内。

字段约束：

| 字段 | 语义 |
|------|------|
| `id` | 唯一标识，调用方必须显式传入 |
| `command` | 仓库相对路径，或 AOSP_WS/KERNEL_WS 内的受控绝对路径；解析后必须仍在受控范围内 |
| `interpreter` | 可选 `bash`/`python3`；调用 argv 允许 `[interpreter, command, ...args]`，helper 校验解释器、命令路径与剩余参数 |
| `args` | 精确参数列表；调用 argv 必须完全匹配（含顺序） |
| `requires_privilege` | 目标命令是否需要提权上下文（自身 sudo 或 helper as_root）；`true` 才 probe/选择 direct/systemd-run，`false` 直接执行 |
| `as_root` | `false`：以当前用户运行，目标脚本自己 sudo；`true`：在 service 内 `sudo -n -- <argv>`；为 true 时 `requires_privilege` 必须为 true |
| `trusted_roots` | 路径类环境变量的静态信任根；键限 `AOSP_WS`/`AOSP_ROOT`/`ANDROID_PRODUCT_OUT`/`KERNEL_WS` |
| `mechanism` | `auto`（默认）、`direct`、`systemd-run`；仅条目内静态配置，不支持环境变量覆盖 |
| `timeout_s` | 超时上限，调用方不得放大 |
| `env_passthrough` | 允许列出但 helper 忽略 `PATH`/`HOME`/`USER` 并用可信值覆盖；不采用调用方值 |
| `reason_required` | 是否必须传非空 reason |
| `enabled` | 默认拒绝；只有 true 才可执行 |
| `destructive` | 破坏性操作标记 |
| `human_confirm` | 破坏性操作需 `confirm_destructive=True` 或环境授权 |
| `approved_by` / `approved_at` | 条目评审元数据；`enabled: true` 的条目必填 |
| `expires_at` | 可选过期日期（ISO 日期）；过期即拒绝 |

路径与参数校验：

- 禁止 `..`、符号链接逃逸、仓库外路径；绝对命令仅允许落在 AOSP_WS/KERNEL_WS。
- 禁止 `shell=True`；argv 原样传给 `subprocess`/`systemd-run`。
- argv 匹配规则：若 entry 声明 `interpreter`，允许 `[interpreter, command, *args]`；
  否则 argv[0] 必须是 `command`。解释器/命令路径先解析为绝对路径再比对，
  防止 `bash -c` 或同名脚本替换。
- 拒绝含换行、NUL、shell 元字符的 argv 项。
- 候选命令必须是普通文件；脚本需可读，直接可执行命令需 `X_OK`。
- 未登记 id、参数不符、条目禁用、条目过期一律拒绝。

### 3.3 执行流程

```text
run(entry_id, argv, ...)
  ├─ 加载 canonical allowlist（harness/config/privileged-allowlist.yaml）
  ├─ 校验 entry_id / command / args / timeout / enabled / destructive /
  │  trusted_roots / reason
  ├─ probe（仅 requires_privilege=true）:
  │    ├─ NoNewPrivs=1 → 跳过父进程 sudo 探测
  │    ├─ sudo -n true 可用 → parent_sudo=true
  │    └─ systemd-run 可用 → 在 transient 内探测 sudo -n true
  ├─ direct:
  │    └─ as_root ? sudo -n -- argv : argv
  ├─ systemd-run:
  │    ├─ systemd-run --user --property=NoNewPrivileges=no \
  │    │   --wait --pipe --collect --property=RuntimeMaxSec=<timeout+30>s \
  │    │   --working-directory=<cwd> --setenv=<allowed env> -- <argv...>
  │    └─ as_root ? sudo -n -- argv : argv
  └─ 写审计 → 返回 RunResult
```

systemd-run 直接接收 argv，不经过 `bash -c`，避免 shell 拼接和注入；
`--setenv=LC_PRIVILEGE_CONTEXT=1` 供目标脚本的递归 guard 识别。目标环境只注入
安全 `PATH`、passwd 解析的 `HOME`/`USER`、`LANG`、`PYTHONNOUSERSITE=1` 及
allowlist 白名单键。

机制选择规则：

- `requires_privilege: false`：不 probe，直接以当前用户执行（`as_root` 必须为 false）。
- `mechanism: direct`：只允许 direct；父进程 sudo 不可用即失败关闭。
- `mechanism: systemd-run`：只允许 transient service；不可用即失败关闭。
- `mechanism: auto`：父进程 sudo 可用时 direct，否则 systemd-run；两者都不可用即失败关闭。
- `NoNewPrivs=1` 时不再在父进程反复探测 `sudo`，直接走 systemd-run。
- `systemd-run` 不可用或 transient 内 sudo 不可用时失败关闭；只输出
  `LC_PRIVILEGE_HELPER` 配置提示，本批不执行外置 helper。
- `mechanism` 只来自 allowlist 条目，不支持环境变量运行时覆盖。

### 3.4 审计

每次调用原子写 `harness/log/privileged/<UTC时间>-<entry_id>-<run_id>-<随机后缀>.json`
（固定目录，新建时目录 0700、已存在则不改权；文件 0600；`harness/log/` 为
gitignore 工作态）：

```json
{
  "schema_version": 1,
  "ts": "2026-10-20T19:27:03Z",
  "entry_id": "package.rpi5.mode0",
  "command": "/mnt/d/Code/Github/AndroidSystemEnhance/harness/scripts/mk_rpi5_full_image.sh",
  "argv": ["/usr/bin/bash", "/mnt/d/Code/Github/AndroidSystemEnhance/harness/scripts/mk_rpi5_full_image.sh", "-mode", "0"],
  "cwd": "/mnt/d/Code/Github/AndroidSystemEnhance",
  "reason": "baseline package",
  "mechanism": "systemd-run",
  "no_new_privs": true,
  "sudo_n": false,
  "sudo_n_parent": false,
  "sudo_n_context": true,
  "context_sudo_ok": true,
  "batch_id": "manual-2610201927",
  "run_id": "a035e0f9...",
  "operator": "lechao",
  "user": "lechao",
  "host": "DESKTOP-RA5VTNU",
  "rc": 0,
  "duration_s": 23.2,
  "dur_s": 23.2,
  "destructive": false,
  "error": ""
}
```

- argv 原样记录；敏感值由调用方在 argv 外传入环境变量，不写入 argv。
- 审计字段不含 `output_tail`；命令输出尾段只进入 `RunResult`/打包证据。
- 拒绝路径（未知 id、参数不符、禁用/过期、破坏性未确认等）也尽力写
  `...-reject-<entry_id>-...json`，含 `code`/`error`，不掩盖原始异常。
- 审计写入失败只告警，不阻断命令；`RunResult.audit_path` 为空并在
  `error` 中带 `PRIV_AUDIT_WRITE_FAILED`。
- `ws_package.py` 证据新增 `privilege` 字段，内嵌 allowlist id、机制、审计路径、
  sudo/NNP 探测；保留 `sudo_n` 兼容旧收据。
- 审计文件不随收据入库；收据只引用路径/摘要，避免证据链依赖 gitignore 域。

### 3.5 失败语义

| 错误码 | 场景 | 处理 |
|--------|------|------|
| `PRIV_ALLOWLIST_MISSING` / `PRIV_ALLOWLIST_INVALID` | allowlist 缺失/不可读/YAML/结构非法 | 失败关闭，提示路径 |
| `PRIV_ENTRY_UNKNOWN` | id 未登记 | 失败关闭 |
| `PRIV_ENTRY_DISABLED` | enabled=false | 失败关闭，提示启用流程 |
| `PRIV_ENTRY_EXPIRED` | 超过 `expires_at` | 失败关闭 |
| `PRIV_SCHEMA` / `PRIV_ARG_MISMATCH` | 字段/参数/命令/路径不符 | 失败关闭，打印期望 argv |
| `PRIV_COMMAND_DENIED` / `PRIV_INTERPRETER_MISSING` | 命令越界/不可读/解释器缺失 | 失败关闭 |
| `PRIV_ENV_PATH_DENIED` | 路径变量越过 `trusted_roots` | 失败关闭 |
| `PRIV_REASON_REQUIRED` | 缺 reason 或超长 | 失败关闭 |
| `PRIV_DESTRUCTIVE_DENIED` | 破坏性未确认 | 失败关闭，提示人工确认方式 |
| `PRIV_SUDO_UNAVAILABLE` | direct/transient 内 sudo 均不可用 | 失败关闭，打印探测 detail |
| `PRIV_SYSTEMD_UNAVAILABLE` | direct/transient sudo 均不可用（含 systemd-run 缺失） | 失败关闭，仅提示外置 helper（未实现） |
| 超时 | 超过 `timeout_s` | 终止进程组，rc=124，error 含 `PRIV_TIMEOUT` |
| `PRIV_AUDIT_WRITE_FAILED` | 审计写失败 | 告警，不阻断 |

`mechanism: auto` 下 systemd-run 缺失且 direct sudo 不可用时，实际按
`PRIV_SUDO_UNAVAILABLE` 返回；`PRIV_SYSTEMD_UNAVAILABLE` 用于条目强制
systemd-run 且 systemd-run 缺失的场景。

所有失败都不进入交互式密码输入，不重试，不降级为裸 sudo。CLI 对参数/allowlist
错误返回 2，对机制不可用返回 3，对命令失败（含超时）返回 1。

## 4. 集成点

### 4.1 `ws_package.py`

- 删除父进程 `sudo -n true` 探测与直接 `subprocess.run(["bash", script])`。
- 改为：

```python
result = privileged_exec.run(
    "package.rpi5.mode0",
    ["bash", str(_SCRIPT), "-mode", "0"],
    reason="ws_package",
    timeout=timeout,
    cwd=project_root(),
    env=build_env(),
)
```

- 保留 BLD-007 前置校验（脚本 sudo 行显式传 `TARGET_PRODUCT`/`ANDROID_PRODUCT_OUT`）。
- 证据字段：保留顶层 `sudo_n`（父进程 direct 探测）与 `script_rc`/`output_tail`；
  新增 `privilege`：`allowlist_id`、`mechanism`、`parent_sudo_n`、`no_new_privs`、
  `systemd_run`、`usable`、`context_sudo_ok`、`audit`/`audit_file`、`audit_error`、
  `rejected_code`、`detail`。失败时 `error` 指向 allowlist 条目与
  `privileged_exec.py --check`。
- `run=False` 的预检路径不触发特权执行。

### 4.2 `ws_verify_chain.py`

- package 步骤删除 ad-hoc `systemd-run` 包装，直接执行
  `python3 ws_package.py --mode 0`；由 `ws_package` 内部选择机制。
- 这样避免“systemd-run 套 systemd-run”，也保证直接 CLI 与链式路径行为一致。
- 其余步骤（sync/build/push/unit_test/acceptance）不变。

### 4.3 `mk_rpi5_full_image.sh`

- 在 `SCRIPT_DIR` 定义后、`sudo rpi5-mkimg.sh` 调用前加 guard（仅 mode 0；
  其他模式保持原行为）：

```bash
if [ "${LC_PRIVILEGE_CONTEXT:-}" != "1" ] \
   && [ "${1:-}" = "-mode" ] && [ "${2:-}" = "0" ] \
   && grep -q '^NoNewPrivs:[[:space:]]*1' /proc/self/status 2>/dev/null; then
  exec python3 "$SCRIPT_DIR/../lib/privileged_exec.py" \
    --entry-id package.rpi5.mode0 --reason "mk_rpi5_full_image direct" \
    -- bash "$SCRIPT_DIR/$(basename "$0")" "$@"
fi
```

- 已有 `sudo TARGET_PRODUCT=... ./rpi5-mkimg.sh` 保留（BLD-007），它在 transient
  service 内正常提权。
- `LC_PRIVILEGE_CONTEXT=1` 防递归。

### 4.4 `baseline_register.py` / `publish_main_base.sh`

- 不直接 sudo；更新 `sudo_n=false` 告警文案，指向
  `privileged_exec.py --check` 与 allowlist，而不是只让用户去会话外。
- `--prepare` 的 package 门禁逻辑不变；打包证据继续由 `script_rc=0` 推导 PASS。
- `publish_main_base.sh` 文档说明：阶段 2/打包自动走 allowlist，人工会话外仅是回退。

### 4.5 破坏性刷机模板

- 新增 `harness/scripts/flash_rpi5_boot.sh`（仓库内受控 wrapper）：
  - 只接受 `/dev/sdXn` 分区；拒绝整卡 `/dev/sdX`；
  - 镜像路径必须在 `ANDROID_PRODUCT_OUT` 内，且经 `readlink -f` 后仍在该根内；
  - 通过 `lsblk -no RM` 校验 `removable=1`，通过 `blockdev --getsize64` 校验
    镜像大小不超过目标分区容量；
  - 打印目标设备与镜像路径，要求 `LC_PRIVILEGE_ALLOW_DESTRUCTIVE=YES` 且
    `--confirm`；
  - 内部 `dd`/`sync`（不自动 `eject`）。
- allowlist 条目 `flash.rpi5.boot_dd` 默认 `enabled: false`、`destructive: true`、
  `human_confirm: true`；启用需评审 + 显式环境授权。
- `harness/reference/incremental-dev-reference.md` 的旧手工命令保留为回退，并标注
  “优先使用 allowlist wrapper”。

### 4.6 文档与规则

- 新增 `harness/rules/privileged-exec.md`：
  - `PRIV-001`：所有 host root 操作必须走 `privileged_exec` + allowlist；禁止在
    harness 脚本中新增裸 `sudo`（allowlist 目标脚本自身的 sudo 除外）。
  - `PRIV-002`：allowlist 默认拒绝、精确命令/参数、路径限定（`trusted_roots`）、
    无 shell 字符串、危险环境变量 denylist。
  - `PRIV-003`：NNP=1 会话内只用 transient unit
    （`systemd-run --user --property=NoNewPrivileges=no`），父进程可 sudo 时才
    direct；不持久修改 opencode service；失败关闭，不等待密码。
  - `PRIV-004`：破坏性条目默认禁用，启用需评审 + 人工确认 + 审计。
- 更新 `harness/reference/build-reference.md` 的 BLD-013：直接 sudo 仍不可用；
  首选 allowlist 自动绕过，会话外人工为回退；给出 `--check`/`--list` 用法。
- 更新 `harness/skills/workspace-verify/SKILL.md`「打包（allowlist 自动，BLD-013）」
  与 `harness/skills/publish-main-base/SKILL.md` 的 package 路径说明。
- `harness/README.md` 增加入口与规则索引（已落地）。

## 5. 安全分析

- **防任意提权**：allowlist 默认拒绝；命令、参数、路径、超时全部精确匹配；
  不接受 `shell=True` 或 `sudo <任意命令>` 透传。
- **防路径逃逸**：解析 `..`/symlink 后必须仍在仓库内；绝对命令仅限
  AOSP_WS/KERNEL_WS；路径类环境变量受 `trusted_roots` 约束。
- **防注入**：argv 原样传递；拒绝换行/NUL/元字符。
- **最小权限**：transient service 以当前用户运行；root 仅来自目标脚本的 NOPASSWD
  sudo；`as_root: true` 仅用于受控 wrapper。
- **不持久化**：`systemd-run --collect` 临时 unit，不改 opencode service 或 sudoers。
- **失败关闭**：机制不可用/条目禁用/参数不符一律拒绝，不提示密码、不重试。
- **审计**：每次调用记录 mechanism、sudo 探测、rc、耗时；收据可追溯。
- **破坏性隔离**：默认禁用 + 人工确认 + 设备/镜像路径校验；不因普通任务自动触发。

## 6. 测试设计

### 6.1 单元测试

新增 `harness/lib/tests/test_privileged_exec.py`：

- allowlist：缺失文件、非法 YAML、默认 deny、未知 id、禁用条目、过期条目、
  `enabled: true` 缺 `approved_by`。
- 参数/路径：命令不存在、不可执行、`..` 逃逸、symlink 逃逸、参数顺序/内容不符、
  换行/NUL/元字符。
- 机制：父进程 sudo 可用 → direct；`NoNewPrivs=1` → systemd-run；两者均不可用 →
  失败关闭；`mechanism: direct|systemd-run` 强制路径。
- `requires_privilege: false` 不 probe；`true` + 非 `as_root`（目标脚本自身 sudo）
  在 NNP=1 时仍走 systemd-run。
- `as_root`：direct 下 argv 前加 `sudo -n --`；非 as_root 不加。
- 破坏性：未确认拒绝；确认 + 环境授权才执行。
- 环境：`env_passthrough` 只传白名单键；敏感变量不进入 transient service。
- 审计：成功/失败都写；写失败只告警；字段完整。
- 超时：进程组回收，rc=124，error 含 `PRIV_TIMEOUT`。
- 无 shell 注入：argv 中含空格/分号时不被拆分。

### 6.2 集成测试

- `ws_package.py`：
  - mock helper：断言 entry_id/argv/reason；证据 `sudo_n` 与 `privilege` 字段；
  - helper 失败：证据 `script_rc=null`，错误指向 allowlist/`--check`；
  - BLD-007 违规仍拒绝。
- `ws_verify_chain.py`：
  - package 步骤命令不再以 `systemd-run` 开头；
  - 证据文件仍按 batch_id 落盘。
- `baseline_register.py`：
  - `sudo_n=false` 告警文案包含 `privileged_exec.py --check`；
  - package PASS/UNKNOWN 推导不变。
- `mk_rpi5_full_image.sh`：
  - 在模拟 `NoNewPrivs=1` + 假 systemd-run 下触发 re-exec；
  - `LC_PRIVILEGE_CONTEXT=1` 时不递归。

### 6.3 现场验证

- `privileged_exec.py --check` 在 opencode 会话内输出 JSON 应为：
  `no_new_privs=true`、`sudo_n=false`、`systemd_run=true`、
  `context_sudo_ok=true`、`usable=true`、`mechanism=systemd-run`。
- `ws_package.py --mode 0` 在会话内直接成功，证据 `privilege.mechanism=systemd-run`、
  `privilege.context_sudo_ok=true`、`script_rc=0`、`sudo_n=false`
  （`sudo_n` 只表示父进程 direct 探测，不表示 systemd-run 内 sudo 不可用）。
- 在无 NOPASSWD 环境，`--check` 失败关闭（退出码 3）并在 `detail` 中给出探测
  失败原因，不挂起、不等待密码。
- 破坏性模板在未授权时拒绝，且不触碰任何块设备。

## 7. 兼容与回退

- allowlist 缺失/损坏：默认拒绝；`ws_package` 明确报错，保留会话外人工打包路径。
- 旧收据的 `package` 字段没有 `privilege`：`baseline_register` 不强制要求，
  仍按 `script_rc` 推导，保证历史证据可读。
- allowlist 只读仓库 canonical 文件；不提供 `LC_PRIVILEGE_ALLOWLIST` 运行时覆盖。
- `mechanism` 只来自 allowlist 条目；不提供 `LC_PRIVILEGE_MECHANISM` 运行时覆盖。
- 回退顺序：allowlist 自动 → 明确失败并提示会话外人工执行；外置
  `LC_PRIVILEGE_HELPER` 仅保留提示文本，本批未实现。
- 不改变 `start-opencode-server.sh` 的 `NoNewPrivileges=true`。

## 8. 验收标准

1. `ws_package.py --mode 0` 在 opencode 会话内直接成功，无需手工 `systemd-run`。
2. `ws_verify_chain.py` package 步骤不再自带 `systemd-run`，无双层包装。
3. allowlist 默认拒绝；未知 id、参数不符、禁用条目、路径逃逸全部拒绝并有测试。
4. 每次特权调用落审计 JSON；`ws_package` 证据含 `privilege` 字段。
5. 破坏性条目默认禁用；未授权时拒绝且不执行任何写操作。
6. `privileged_exec.py --check` 能准确报告 direct/systemd-run 能力。
7. 现有 selfcheck、pytest、收据/promote 门禁全绿；历史收据可读。
8. 文档与规则（`PRIV-001`~`PRIV-004`、BLD-013、两个 SKILL）同步更新。

## 9. 实施拆分（供 writing-plans 使用）

1. **P0 核心**：`privileged_exec.py` + allowlist + 单元测试；`ws_package.py` 接入；
   `ws_verify_chain.py` 去包装；`mk_rpi5_full_image.sh` guard。
2. **P0 文档**：`privileged-exec.md` 规则、BLD-013、workspace-verify/publish-main-base SKILL。
3. **P1 破坏性模板**：`flash_rpi5_boot.sh` + 禁用 allowlist 条目 + 测试（不启用）。
4. **P1 加固（下一批）**：`check_privileged.py` 扫描非 allowlist 的裸 `sudo`，接入
   selfcheck 新 rc；本批不做，避免 `REQUIRED_RC_KEYS`/CI 解析/`test_workflow_ci.py`
   同时扩散，下一批单独评审。
5. **验证**：单元/集成测试 + 现场 `--check` + 真实 `ws_package` 打包 + 收据覆盖。

## 10. 本批明确不做

- `check_privileged.py` 静态扫描与 selfcheck 新 rc：下一批。
- 整卡 `/dev/sdX` 刷写：首期只做 boot 分区 wrapper 模板，整卡流程明确后再评审。
- 外置 `LC_PRIVILEGE_HELPER` 的具体实现：本批只保留失败提示，不提供
  root-owned wrapper / system service。
- 自动修改 `/etc/sudoers`：不实现，也不打印未经验证的 sudoers 配置行。

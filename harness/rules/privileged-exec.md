# 特权操作 allowlist

> **规则 ID**: `PRIV-001` ~ `PRIV-004`
> **适用范围**: harness 内所有需要 host root 的操作。
> **入口**: `harness/lib/privileged_exec.py`
> **配置**: `harness/config/privileged-allowlist.yaml`

## 加载时机

新增/修改 harness 内 host root 操作、allowlist 条目、打包或刷写路径，或排查
`NoNewPrivileges` 下 sudo 失败、提权机制与审计问题前，必须先加载本规则。

## 规则

- **PRIV-001**：所有 host root 操作必须经 `harness/lib/privileged_exec.py` +
  `harness/config/privileged-allowlist.yaml`；禁止在 harness 脚本中新增裸 `sudo`
  （allowlist 目标脚本自身的 sudo 除外）。allowlist 只读仓库内 canonical 文件，
  不提供运行时路径覆盖或测试 allowlist。
- **PRIV-002**：allowlist 默认拒绝；命令/解释器/参数/路径/超时精确匹配；
  禁止 shell 字符串、`..` 逃逸、仓库外路径（AOSP/KERNEL 受控脚本除外）。
  路径类环境变量（`AOSP_WS`/`AOSP_ROOT`/`ANDROID_PRODUCT_OUT`/`KERNEL_WS`）
  只能在条目的 `trusted_roots` 中静态登记；`PATH` 使用固定安全路径，
  `HOME`/`USER` 经 passwd 解析。三者即使列入 `env_passthrough` 也会被 helper
  忽略并用可信值覆盖，不采用调用方值；危险环境变量由 denylist 拒绝。
- **PRIV-003**：`NoNewPrivileges=1` 会话内只用 transient unit
  （`systemd-run --user --property=NoNewPrivileges=no`），不持久修改 opencode
  service 或 sudoers；父进程本身可 sudo 时才允许 `direct`。失败关闭，
  不等待密码、不重试、不降级为裸 sudo。
- **PRIV-004**：破坏性条目默认 `enabled: false`；启用需评审 +
  `approved_by`/`approved_at` + `human_confirm: true`，运行时还须显式
  `--confirm-destructive` 或 `LC_PRIVILEGE_ALLOW_DESTRUCTIVE=YES`，并落审计。
  破坏性 wrapper 必须校验设备、镜像位于 `trusted_roots` 内、设备容量等边界。

## 使用

```bash
python3 harness/lib/privileged_exec.py --list
python3 harness/lib/privileged_exec.py --check
python3 harness/lib/privileged_exec.py --entry-id <id> --reason "<原因>" \
  [--confirm-destructive] -- <argv...>
```

- `--check` 只探测机制能力（父进程 sudo、systemd-run、transient 内 sudo），
  不执行目标命令；机制不可用时退出码为 3。
- CLI 退出码：`0` 成功；`1` 命令失败；`2` 参数/allowlist 错误；
  `3` 机制不可用（失败关闭）。
- 审计固定落 `harness/log/privileged/`（新建时目录 0700、已存在则不改权；
  文件 0600；gitignore 域），不随收据入库；收据只引用审计路径。审计写失败
  只告警，不改变命令 rc。
- 外置 `LC_PRIVILEGE_HELPER` 仅保留失败提示，本批未实现，不得宣称可用。

## 违规后果

- 违反 PRIV-001/PRIV-002：裸 sudo、未登记命令或路径逃逸 → 提权面失控；
  批次退回并收窄到 allowlist 条目，未修复不得上板或发布。
- 违反 PRIV-003：持久修改 opencode service/sudoers 或等待密码 → 环境偏离、
  任务挂起；恢复原配置并改用 transient unit。
- 违反 PRIV-004：破坏性条目默认启用或缺少确认/审计 → 可能误写块设备；
  立即禁用条目并复核设备/镜像校验。

## 相关

- 会话内 sudo 限制与打包路径：[BLD-013](../reference/build-reference.md)
- 打包步骤：[workspace-verify SKILL](../skills/workspace-verify/SKILL.md)
- 破坏性 boot 刷写回退：[INC-004](../reference/incremental-dev-reference.md)

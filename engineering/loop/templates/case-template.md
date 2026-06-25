# Loop Engineering 用例生成模板

> 本文档约束 AI（opencode）从模块代码 + 需求文档生成验收用例时的格式、质量、coverage。

## 1. 用例文件格式规范（YAML schema）

每个用例文件必须包含以下顶层字段：

```yaml
suite: <suite名称，snake_case，与文件名一致>
version: 1

include:           # 可选：引入其他 suite 的 cases 和 collectors
  - common/shell

cases:             # 必填：用例列表
  - id: <用例ID，snake_case，全局唯一>
    description: "<用例描述，一句话说清楚验证什么>"
    command: "<执行的 shell 命令，空字符串表示仅探测 prompt>"
    # 或
    action: <reboot>  # 可选：动作型用例（与 command 互斥）。当前支持 reboot：触发设备重启并等待启动完成
    assert:        # 必填：断言规格（action case 可为空 {}）
      type: <断言类型>
      value: <断言值>  # contains/equals/not_contains 需要
      pattern: <正则>  # regex 需要
    severity: <critical|warn>  # critical=fail阻断，warn=仅记录。默认 critical
    requires: [<前置用例ID>]    # 可选：依赖声明
    on_fail:       # 可选：失败时的动作
      collectors: [<collector名称>]
    tags: [<标签>]  # 可选

collectors:        # 可选：collector 定义
  <collector名称>:
    commands: [<命令1>, <命令2>]
    hints: "<给AI的分析提示>"

final_collectors:  # 可选：suite 级，suite 结束后执行的收集器列表（无论是否有用例失败）
  - <collector名称>   # 如 lcview_pull_logs / lcview_invalid_log
```

### 必填字段 checklist
- [ ] suite（与文件名一致）
- [ ] version
- [ ] cases（至少 1 条）
- [ ] 每条 case 有 id / description / command / assert

## 2. 断言类型选择矩阵

| 场景 | 推荐断言 | 示例 |
|------|---------|------|
| 进程/service 状态 | `contains` | value: "running" |
| IP 地址/网络格式 | `regex` | pattern: "inet \\d+\\.\\d+\\.\\d+\\.\\d+" |
| 布尔属性（0/1） | `equals` | value: "1" |
| shell prompt 可见 | `prompt_visible` | （无参数） |
| 确认无错误输出 | `not_contains` | value: "error" |
| 命令执行成功 | `exit_code_zero` | （无参数） |
| JSON 字段校验 | `json_field` | `{type: json_field, path: "read_bytes", op: "gt", value: 0}` |
| 指定退出码 | `exit_code_equals` | `{type: exit_code_equals, value: 5}` |
| 枚举状态 | `contains_any` | `{type: contains_any, values: ["running", "stopped"]}` |

### 新增断言类型详情

**`json_field`**：解析 JSON 输出，按点号分隔的 path 提取字段，用 op 比较。支持 op：`eq`/`ne`/`gt`/`ge`/`lt`/`le`/`exists`/`not_exists`。path 支持嵌套（`event.type`）。例：`assert: {type: json_field, path: "read_bytes", op: "gt", value: 0}`

**`exit_code_equals`**：校验命令退出码等于指定值。适用于退出码语义化场景（如 fault-verify 用 exit_code=0 PASS，exit_code=5 CHECK_FAIL）。例：`assert: {type: exit_code_equals, value: 0}`

**`contains_any`**：校验输出包含列表中任一项。适用于枚举类状态校验。例：`assert: {type: contains_any, values: ["running", "stopped"]}`

## 3. coverage 要求

生成用例时必须覆盖以下维度（视模块功能而定）：

- **每个 init service**：至少 1 条 `getprop init.svc.<name>` 用例
- **每个公开 HAL 接口**：至少 1 条存在性/可用性用例
- **每个设备节点**：至少 1 条 `ls -l /dev/<node>` 存在性检查
- **关键系统属性**：sys.boot_completed / ro.boottime.* 等必须覆盖
- **网络连通性**：wlan 连接状态、IP 分配、DNS 解析

用例 description 中标注来源：
- `[code]` 来自代码分析
- `[spec]` 来自需求文档

## 4. 命名规范

- suite 名：snake_case，与文件名一致（如 `lcview` 对应 `lcview.yaml`）
- case id：snake_case，suite 内唯一，语义清晰（如 `zygote_running`）
- collector 名：语义化（`crash_dump` / `init_log` / `network_log`）
  - 公共诊断 collector 统一沉淀在 `cases/common/shell.yaml`（`common.shell`
    命名空间），业务 suite include 后用短名引用，不要本地重定义同名 collector

## 5. collector 选择指南

> **首选公共 collector**：`common.shell` 已内置 `boot_log` / `init_log` /
> `crash_dump`，业务 suite 通过 `include: [common/shell]` 即可获得，失败时
> 直接用短名引用即可，**不要重复定义**。仅在场景专属诊断（特定 HAL 日志、
> 模块私有路径等）时才在本地 `collectors:` 块中新增 collector。

### 何时用公共 collector

| 场景 | 用法 |
|------|------|
| 复用 boot/init/crash 诊断 | `include: [common/shell]`，`on_fail.collectors: [boot_log]` 等 |
| 全新的场景专属诊断 | 本地 `collectors:` 定义短名（仍走短名 → FQN 解析） |
| 同时需要公共与本地 | include + 本地定义；引用都写短名即可 |

### 常见 fail 类型对照

| fail 类型 | 推荐 collector | 典型命令 | 公共/本地 |
|-----------|--------------|---------|----------|
| 进程崩溃/abort | `crash_dump` | logcat -b crash -d, ls /data/tombstones/ | 公共 |
| 服务未启动/异常退出 | `init_log` | getprop init.svc.*, logcat -b system -d | 公共 |
| boot 卡死/时序问题 | `boot_log` | dmesg, getprop ro.boottime.* | 公共 |
| 网络问题 | `network_log` | ip addr, logcat -b system -d, ping | 本地 |
| SELinux/权限问题 | `security_log` | getenforce, dmesg | grep avc | 本地 |

## 6. 好用例 vs 坏用例

### 好用例（确定性、可重复、单一职责）

```yaml
- id: zygote_running
  description: "zygote 处于 running 状态"
  command: "getprop init.svc.zygote"
  assert: {type: contains, value: "running"}
  severity: critical
```

### 坏用例（模糊、多职责、不可重复）

```yaml
- id: system_ok          # 太模糊
  description: "系统正常"  # 不具体
  command: "getprop && dmesg && logcat"  # 一条用例查太多
  assert: {type: not_contains, value: "error"}  # 不精确
```

## 7. 生成 checklist

- [ ] 每条用例有清晰的 description
- [ ] severity 明确（critical/warn）
- [ ] 依赖声明完整（requires 拓扑无环）
- [ ] on_fail 指定合理 collector
- [ ] 命名符合 snake_case
- [ ] suite/version 字段存在
- [ ] coverage 覆盖所有关键功能点
- [ ] 用例 description 标注来源（code/spec）

## 8. 参数化原子用例

当多个用例结构相同、仅参数不同时，使用参数化展开避免复制 YAML。

### 基本用法

```yaml
parameters:
  services: [zygote, surfaceflinger, netd]

cases:
  - id: service_running
    foreach: services
    command: "getprop init.svc.${item}"
    assert: {type: contains, value: "running"}
```

展开后生成 3 条用例：`service_running_zygote`、`service_running_surfaceflinger`、`service_running_netd`。

### 规则

- `foreach` 引用 `parameters` 中定义的列表
- `${item}` 在 `command` / `description` / `assert.value` / `assert.pattern` 中替换
- 无 `foreach` 的用例不受影响
- 展开后 FQN 重复会在加载阶段报错

### 参数取值

`parameters` 的值必须是 list，列表项可以是简单字符串：

```yaml
parameters:
  critical_props:
    - key: sys.boot_completed
      value: "1"
```

注意：当列表项为复杂结构（dict 等）时，`{原id}_{item}` 中的 `item` 会取其
字符串形式（可能不直观），推荐仅用简单字符串/数字作为 foreach 列表项。

## 9. action 动作型用例

当用例需要触发设备状态变迁（如重启）而非执行命令时，用 `action` 字段替代 `command`。

### 支持的 action 值

| action | 行为 | 适用场景 |
|--------|------|---------|
| `reboot` | 触发设备重启并等待启动完成（三级渐进判定：L1 boot 开始 → L2 init 阶段 → L3 boot_completed 验证） | boot 诊断、启动问题复现 |

### 示例

```yaml
cases:
  - id: trigger_reboot
    action: reboot
    description: "触发设备重启并等待启动完成"
    severity: critical
    assert: {}

  - id: boot_ok
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    requires: [trigger_reboot]   # 拓扑保证：reboot 完成后才跑
```

### 规则

1. `action` 与 `command` **互斥**，二选一
2. `action: reboot` 的 case **不需要 assert value**（assert 可为空 `{}`）
3. 后续 case 靠 `requires: [trigger_reboot]` 拓扑保证在 reboot 完成后执行
4. reboot_and_wait 的判定 marker 来自 DeviceProfile（`boot_markers` / `panic_markers`）
5. action case 的 TestCaseResult.assertion 字段为 `{"type": "action", "action": "reboot"}`

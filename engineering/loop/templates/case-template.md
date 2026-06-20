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
    assert:        # 必填：断言规格
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

## 5. collector 选择指南

| fail 类型 | 推荐 collector | 典型命令 |
|-----------|--------------|---------|
| 进程崩溃/abort | `crash_dump` | logcat -b crash -d, ls /data/tombstones/ |
| 服务未启动/异常退出 | `init_log` | getprop init.svc.*, logcat -b system -d |
| 网络问题 | `network_log` | ip addr, logcat -b system -d, ping |
| boot 卡死/时序问题 | `boot_log` | dmesg, getprop ro.boottime.* |
| SELinux/权限问题 | `security_log` | getenforce, dmesg | grep avc |

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

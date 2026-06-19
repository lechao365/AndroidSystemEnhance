# loop 串口 shell 基础链路修复设计

> **日期**：2026-06-19
> **状态**：已确认，待评审
> **范围**：修复 `engineering/loop/` 中 rp5-serial provider 与 `boot-failure-debug-loop` 的基础交互链路，使 loop 能稳定识别树莓派 5 已存在的串口 shell、执行 L1 只读命令并采集输出，为后续 service 反复启动定位提供可靠底座。

---

## 1. 背景

当前树莓派 5 实际具备串口 shell，人工通过 Windows MobaXterm 可以登录并执行命令；但 `loop` 的 live workflow 仍无法稳定识别和使用该 shell。

已确认的代码症状：

1. provider host 只把带 `\n` 的完整行写入 recent buffer，未换行 prompt 可能一直停留在半行缓存中，workflow 无法看到。
2. `AutomationClient` 未建立稳定的实时流订阅/消费语义，导致 workflow 不能像人工终端一样可靠等待新输出。
3. `send_line()` 没有消费 host 协议响应，后续读取可能混入协议 JSON，而不是纯串口文本。
4. workflow 虽规划了 `send_enter -> wait_prompt`，但 `wait_prompt` 实际未执行；`login_prompt_not_reached` 分支也不会在回车后重新观察。
5. L1 命令当前只发送不采样，报告无法携带真实命令证据。

因此，问题不在于“板子没有串口 shell”，而在于 **provider 与 workflow 的交互框架尚未形成完整闭环**。

---

## 2. 本次目标

本次只修基础框架，不直接实现 service 重启定位逻辑。目标限定为：

1. loop 能稳定识别 **已存在的串口 shell prompt**。
2. `send_enter` 后 workflow 能真正等待 prompt，而不是占位跳过。
3. L1 只读命令（`dmesg/getprop/mount/ps`）能够：
   - 发送到串口 shell
   - 采集对应输出
   - 把关键证据写入报告
4. 为下一阶段“service 反复启动定位”预留清晰扩展点，但本次不加入 service parser。

---

## 3. 非目标

本次明确不做：

1. 新增 ADB provider 或 serial/adb 自动切换。
2. 实现账号/密码登录流程自动化。
3. 实现 service restart parser、service 名提取、失败原因聚合。
4. 扩展 L3/L4 动作。
5. 改造 `engineering/harness/` 框架。

---

## 4. 根因归纳

### 4.1 prompt 可见性断点

`RuntimeState.read_lines()` 只会把按 `\n` 切分得到的完整行写入 `_line_buffer`；最后一段未换行内容保存在 `_rx_buf`。shell prompt 往往不带换行，因此 prompt 可能长期不可见。

### 4.2 协议响应与串口文本混流

`AutomationClient.send_line()` 当前仅发送请求，不读取响应；后续 `read_until_timeout()` 直接从同一 `makefile.readline()` 读取，可能把 host `OK` 响应误当作串口输出。

### 4.3 workflow 动作闭环缺失

`wait_prompt` 在动作执行层被硬编码为 `SKIP`；`login_prompt_not_reached` 分支也不会在 `send_enter` 后再次观察和重新分类，因此人工可用的“回车唤起 prompt”路径无法在 loop 中成立。

### 4.4 L1 只发不收

当前 L1 动作仅 `send_line(command)`，没有附带输出采样与报告集成，导致即使命令执行成功，workflow 也无法利用这些证据。

---

## 5. 设计原则

1. **先修底层交互语义，再修 workflow 行为**，避免上层靠猜测补丁掩盖 provider 缺陷。
2. **协议响应与串口流严格分离**，workflow 只消费纯串口文本。
3. **保持 V1 边界**：只完成 shell 可达与 L1 采样闭环，不引入更激进动作。
4. **优先兼容已有 profile/rules 结构**，避免无关重构。
5. **所有改动必须可通过离线测试覆盖关键回归场景**。

---

## 6. 总体方案

本次采用“**provider + workflow 一起补齐**”方案，分为三段：

1. **provider 输入/输出链路修复**
2. **workflow prompt 等待与重观察闭环补齐**
3. **L1 命令输出采样与报告落地**

这样可以保证：
- provider 能可靠看到 prompt 和命令输出
- workflow 能在 live 场景下真正等待 shell
- 报告能携带真实采样结果

---

## 7. provider 层设计

### 7.1 Host：补充半行可见性

目标文件：
- `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py`

设计：

1. 保留现有“完整行 recent buffer”能力，不破坏已存在行为。
2. 新增“pending 半行快照”读取能力，用于暴露当前 `_rx_buf` 中尚未换行但已接收到的文本。
3. `stream.read_recent` 返回结果需要能把 **最近完整行 + 可见 pending 文本** 一并提供给上层。
4. pending 文本仅作为观察证据，不写回 `_line_buffer`，避免伪造换行语义。

结果：
- shell prompt 即使未换行，也可被 workflow 检测到。
- 不改变 Host 对串口原始时序的处理方式。

### 7.2 AutomationClient：建立明确的协议/流分离

目标文件：
- `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py`

设计：

1. 在连接建立后显式订阅 `stream.subscribe`，使 host 开始推送 `stream.data`。
2. 引入统一的消息读取分发逻辑：
   - 协议响应（如 `writer.acquire` / `input.send_line` / `stream.read_recent` 的 OK/ERROR）
   - 流事件（`stream.data`）
   不能再混用同一层语义。
3. `send_line()` 必须读取并校验对应响应；失败时抛出异常或返回明确错误。
4. `read_until_timeout()` 只返回 `stream.data` 中的串口文本，不再读取协议响应文本。
5. `capture_recent_lines()` 继续通过请求/响应获取 host recent buffer，但其返回结果需要兼容 pending prompt 文本。

结果：
- workflow 读取的将是纯串口输出。
- live 等待逻辑具备稳定基础。

### 7.3 兼容性要求

1. 不破坏现有 `AutomationClient` 的上下文管理器用法。
2. 不改变 `writer.acquire/release` 的现有外部调用方式。
3. interactive client 本次不要求一起重构，但若共享底层分发逻辑成本低，可复用同一机制；否则保持聚焦，仅修 automation 链路。

---

## 8. workflow 层设计

### 8.1 wait_prompt 从占位改为真实动作

目标文件：
- `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py`
- `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py`

设计：

1. `wait_prompt` 不再在 `execute_action()` 中直接 `SKIP`。
2. 由 runner 在动作阶段识别 `wait_prompt`，调用 `transport.wait_for_pattern()`。
3. 匹配模式直接复用 `cfg.prompt_markers`，避免重复定义第二套规则。
4. 超时未命中时保留当前失败结论；命中时进入重新观察流程。

结果：
- `send_enter -> wait_prompt` 形成真实链路。

### 8.2 login_prompt_not_reached 的重观察闭环

设计：

1. 当初始分类为 `login_prompt_not_reached` 时：
   - 执行 `send_enter`
   - 执行 `wait_prompt`
2. 若命中 prompt：
   - 立即再次 `capture_snapshot()`
   - 重新跑 `evaluate_rules()/classify()`
3. 若重分类变为 `shell_prompt_available`：
   - 继续执行 L1 只读采样
   - 最终成功退出
4. 若仍未命中 prompt：
   - 保持原失败结论
   - 报告中注明已尝试 send_enter/wait_prompt

结果：
- workflow 能覆盖“人工按回车即可出现 shell”的真实场景。

### 8.3 保持其他分类边界不变

本次不改动以下规则语义：
- `kernel_panic_detected`
- `reboot_loop_detected`
- `kernel_boot_hang`
- `no_output_after_attach`

只补齐 `login_prompt_not_reached -> shell_prompt_available` 的转化闭环。

---

## 9. L1 命令采样设计

目标文件：
- `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py`
- `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py`
- `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py`
- `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py`

设计：

1. L1 命令执行流程从“只发送”扩展为：
   - 发送命令
   - 在限定窗口内抓取输出
   - 将输出片段附加到动作记录或专门的 evidence 结构
2. 每条命令输出只保存必要证据：
   - 原始文本片段
   - 可选行数限制
   - 采样是否超时/为空
3. 本次不做复杂语义解析；只保证证据可追溯。
4. 报告中新增 L1 采样摘要区：
   - 命令名
   - 结果状态
   - 关键输出前若干行

结果：
- 后续 service 分析可直接基于已采样证据演进，而不必重写执行框架。

---

## 10. 数据模型调整

### 10.1 ActionRecord 扩展

需要为动作记录补充证据承载能力，至少支持：

- `output_lines: list[str]` 或等价字段
- `metadata: dict[str, str | int | bool]`（如采样窗口、是否命中 prompt）

要求：
- 不破坏现有 summary/report 渲染
- 序列化为 `report.json` 时可直接输出

### 10.2 Snapshot / Report 保持兼容

1. 现有 snapshot 结构继续表示观察窗口，不混入 L1 命令输出。
2. L1 命令输出作为动作证据归档，避免语义混淆。
3. `report.json` 与 `summary.txt` 都应出现 L1 采样结果摘要。

---

## 11. 测试设计

### 11.1 provider 测试

新增/扩展测试覆盖：

1. **半行 prompt 可见**
   - recent buffer 在存在 `_rx_buf` 时能返回 pending prompt 文本
2. **send_line 响应被正确消费**
   - `input.send_line` 的 OK/ERROR 不会混入后续串口文本读取
3. **订阅实时流后 read_until_timeout 只返回 stream.data 文本**
4. **capture_recent_lines 与实时流可组合工作**

### 11.2 workflow 测试

新增/扩展测试覆盖：

1. 初始 `login_prompt_not_reached`，`send_enter + wait_prompt` 后转为 `shell_prompt_available`
2. `wait_prompt` 超时仍保持失败结论
3. L1 命令执行后动作记录带有输出证据
4. 报告包含命令输出摘要

### 11.3 回归要求

以下既有场景必须保持通过：

1. normal boot fixture
2. panic fixture
3. hang fixture
4. reboot loop fixture
5. no output fixture

---

## 12. 实施顺序

建议顺序：

1. 先写 provider 层失败测试
2. 修 host pending prompt 可见性
3. 修 automation 协议/流分离与订阅
4. 跑 provider 测试
5. 再写 workflow 失败测试
6. 落地 `wait_prompt`、重观察、L1 输出采样
7. 更新报告输出
8. 跑全量 workflow 测试
9. 最后做 live 真机验证

---

## 13. 成功标准

满足以下条件即视为本次目标达成：

1. live 模式下，树莓派串口 shell 已存在时，workflow 不再稳定误判为 `login_prompt_not_reached`。
2. `send_enter` 后 workflow 能真实等待 prompt，并在命中后重新观察。
3. `dmesg/getprop/mount/ps` 至少能采集到可见输出或明确的空输出结果。
4. 报告中可见命令级证据摘要。
5. 既有离线 fixture 与新增回归测试全部通过。

---

## 14. 风险与控制

### 风险 1：协议改动影响现有 client 行为
控制：优先限制在 `AutomationClient`，不在本次大改 `interactive.py`。

### 风险 2：pending prompt 暴露方式破坏 recent buffer 语义
控制：pending 文本只作为附加观察结果返回，不写入 `_line_buffer`。

### 风险 3：L1 输出采样把普通串口背景日志与命令输出混在一起
控制：本次只保证“可见证据采样”，不保证精确命令边界；后续 service diagnosis 再引入更强的命令界定策略。

### 风险 4：live 场景时序不稳定
控制：先用 provider/workflow 单测锁定行为，再做最小真机验证。

---

## 15. 后续演进

本次完成后，可在同一框架上继续扩展：

1. service restart diagnosis workflow 或 rule set
2. 针对 Android init/service 的日志提取器
3. 更精确的命令输出边界识别
4. serial 与 adb 的双通道接管策略

本次设计刻意不提前实现这些能力，避免在基础链路不稳定时叠加复杂度。

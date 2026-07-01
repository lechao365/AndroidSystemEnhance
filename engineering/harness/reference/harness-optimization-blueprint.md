# Harness Optimization Blueprint

> **AI 读取指引**：本蓝图是 `engineering/harness/` 的总体优化纲领，融合行业优秀实践与
> 本项目特有实践。作为后续所有优化工作的单一参考源。非约束性参考，不替代 rules/ 中的硬规则。

## 1. 行业优秀实践矩阵

按六大维度组织，每个实践标注来源工具和当前实现状态。

### 1.1 规则系统（Rules System）

| ID | 实践 | 描述 | 来源 | 状态 |
|----|------|------|------|------|
| R-01 | 条件式规则匹配 | 规则按 glob 模式匹配生效，只有 agent 操作匹配路径时才注入上下文 | Cursor `.cursor/rules/*.mdc` | ❌ 未实现 |
| R-02 | 按层级的多级规则链 | 用户设置 > 组织级 > 项目级 > 文件级，高优先级覆盖低优先级 | VS Code Copilot / OpenCode | ⚠️ 部分实现 |
| R-03 | 声明式规则索引 | 规则索引文件统一管理所有规则的 ID、适用范围、加载条件 | 本项目已有 | ✅ 已实现 |
| R-04 | 规则模板标准化 | 每个规则文件使用固定 frontmatter 定义 ID、scope、description | VS Code `*.instructions.md` | ✅ 已实现 |
| R-05 | instruction 数组外部引用 | 通过配置引用多个规则文件，支持 glob 和远程 URL | OpenCode `opencode.json instructions` | ✅ 已实现 |

### 1.2 上下文管理（Context Management）

| ID | 实践 | 描述 | 来源 | 状态 |
|----|------|------|------|------|
| C-01 | 智能上下文压缩 | 接近 token 限制时自动压缩历史，保持会话连贯性 | Claude Code / OpenCode / Copilot CLI | ❌ 未提示 |
| C-02 | 上下文可视化 | `/context` 展示 token 用量分解 | OpenCode / Copilot CLI | ❌ 未实现 |
| C-03 | 按需引用 / 延迟加载 | Skills 和 Rules 按条件触发才加载，不占用基础上下文 | OpenCode Skills / VS Code instructions | ❌ 未实现 |
| C-04 | 会话持久化与检索 | 会话历史持久化，支持自然语言查询过往会话 | Copilot CLI Chronicle | ❌ 未实现 |
| C-05 | 自动生成项目上下文 | `/init` 自动扫描仓库生成 AGENTS.md | OpenCode | ✅ 已有（平台特性） |
| C-06 | 外部引用注入 | 配置外部目录或 Git 仓库作为引用上下文 | OpenCode References | ✅ 已实现 |

### 1.3 工作流编排（Workflow Orchestration）

| ID | 实践 | 描述 | 来源 | 状态 |
|----|------|------|------|------|
| W-01 | Plan Agent 分治 | 只读 Plan agent 先分析和规划，确认后交给 Build agent 执行 | OpenCode / VS Code Plan Agent | ❌ 未实现 |
| W-02 | 四阶段端到端工作流 | Research → Plan → Code → PR 完整链路 | GitHub Copilot Workspace | ⚠️ 部分实现 |
| W-03 | Todo 跟踪 | 自动化跟踪进度，Plan agent 生成 todo list | VS Code Plan Agent | ❌ 未实现 |
| W-04 | 子 agent 并行 | 独立子任务通过子 agent 并行处理 | Anthropic Sub-agents / OpenCode | ✅ 已实现 |
| W-05 | 工作流模板化 | WORKFLOW.md 契约定义步骤、输入输出、验证条件 | 本项目已有 | ✅ 已实现 |

### 1.4 工具链集成（Tool Chain Integration）

| ID | 实践 | 描述 | 来源 | 状态 |
|----|------|------|------|------|
| T-01 | MCP 统一接口 | 通过 MCP 协议暴露外部工具、资源、prompts | 行业标准 | ❌ 未实现 |
| T-02 | Hooks 生命周期 | pre/post 钩子覆盖完整 agent 事件周期 | VS Code / Copilot CLI | ❌ 未实现 |
| T-03 | 自定义工具扩展 | 工具目录 + 脚本实现，支持任意语言编写 | OpenCode `.opencode/tools/` | ❌ 未实现 |
| T-04 | Agent Skills 开放标准 | SKILL.md + YAML frontmatter 定义可复用工作流 | OpenCode / VS Code / Copilot | ❌ 未实现 |

### 1.5 权限和安全控制（Permission & Security）

| ID | 实践 | 描述 | 来源 | 状态 |
|----|------|------|------|------|
| P-01 | 三级权限模型 | allow / ask / deny 三态控制每个工具操作 | OpenCode | ✅ 已实现（access 五级扩展） |
| P-02 | 通配符路径匹配 | 支持 `*` `?` 通配符对路径和指令细粒度控制 | OpenCode | ❌ 未实现 |
| P-03 | 外部目录访问控制 | 明确配置 agent 能访问工作目录外的哪些目录 | OpenCode | ✅ 已实现（harness-paths.conf） |
| P-04 | 沙箱隔离 | 限制文件系统和网络访问的沙箱环境 | VS Code / Copilot CLI | ❌ 未实现 |
| P-05 | 自动模式 | `--auto` 标志自动批准非拒绝请求，适合 CI 场景 | OpenCode | ❌ 未实现 |
| P-06 | 安全输出验证 | AI 输出经过威胁检测扫描后才执行 | GitHub Agentic Workflows | ❌ 未实现 |

### 1.6 代码与文档质量管理（Quality Management）

| ID | 实践 | 描述 | 来源 | 状态 |
|----|------|------|------|------|
| Q-01 | 预提交/后编辑钩子 | 编辑后自动格式化、lint、类型检查 | Anthropic Hooks / VS Code Hooks | ❌ 未实现 |
| Q-02 | 测试先行的统一入口 | 统一的测试运行器 + 公共断言库 | 本项目已有 | ✅ 已实现 |
| Q-03 | 配置校验自动化 | YAML 可解析性检查 + 字段合法性校验 | 本项目已有 | ✅ 已实现 |
| Q-04 | 文档与代码一致性检查 | 文档链接验证 + 文件清单一致性 + 契约校验 | 本项目已有 | ✅ 已实现 |

## 2. 本项目特有优秀实践

以下实践是本项目在 `engineering/harness/` 中独创或强化的，值得保留并持续完善：

| 实践 | 实现位置 | 说明 |
|------|----------|------|
| Manifest 声明式索引 + 任务准入 | `rules/manifest.yaml` | context + rules + access 三位一体，AI 一步完成任务判定 |
| Access 五级控制 | `rules/manifest.yaml` 每个 context | 独创的 direct_edit / workflow / require_plan / require_confirmation / require_evidence 控制 |
| 多语言统一路径工具 | `lib/shell/`, `lib/python/`, `lib/bat/` | shell / python / bat 三语言共用一个路径事实源 |
| 基线（baseline）晋升与回退 | `config/baseline-*.yaml` | 有证据模板、状态登记、晋升机制 |
| Observability 公共库 | `lib/shell/harness_observability.sh` | 统一日志/step/artifact/错误捕获 |
| 工作流契约化 | `workflows/*/WORKFLOW.md` | 每个工作流有独立契约文档 + 配套测试 |
| 配置静态校验流水线 | `scripts/validate_harness_*.sh` | 三个独立校验器覆盖 config / docs / scripts |
| 测试框架 + 夹具 | `tests/` | 统一运行器 + 断言库 + fixture 驱动的契约测试 |

## 3. 差距分析

| 实践 | 当前状态 | 优先级 | 建议方向 | 预计工作量 |
|------|----------|--------|----------|-----------|
| R-01 条件式规则匹配 | ❌ | P0 | manifest.yaml path 匹配升级为 glob 条件注入，按路径自动决定加载哪些 rules | 中 |
| W-01 Plan Agent 分治 | ❌ | P0 | 在 AGENTS.md / manifest 中声明 plan-only context，减少 Safety 提示 | 小 |
| T-04 Agent Skills | ❌ | P0 | 将 reconcile/revert/sync/git-push 流程打包为可发现 Skill | 中 |
| R-02 多级规则链 | ⚠️ | P1 | 建立用户 > 项目 > 文件级优先级覆盖链 | 中 |
| W-02 四阶段工作流 | ⚠️ | P1 | WORKFLOW.md 增加 Research/Plan/Code/PR 阶段声明 | 小 |
| W-03 Todo 跟踪 | ❌ | P1 | 在 PLAN.md / WORKFLOW.md 中标准化 todo 跟踪格式 | 小 |
| C-03 条件加载 | ❌ | P1 | manifest context 映射到平台原生 Skills/Rules 机制 | 中 |
| Q-01 预提交/后编辑钩子 | ❌ | P1 | 通过 Hooks 实现编辑后自动格式化/lint | 小 |
| T-01 MCP 接口 | ❌ | P2 | 定义 MCP server 暴露 validate、路径查询等 harness 能力 | 大 |
| T-02 Hooks 生命周期 | ❌ | P2 | 在 harness 脚本中引入 pre/post 钩子模式 | 中 |
| T-03 自定义工具 | ❌ | P2 | 将 harness 公共库暴露为 `.opencode/tools/` | 中 |
| P-04 沙箱隔离 | ❌ | P2 | 在 AGENTS.md 中定义文件操作安全边界 | 小 |
| P-06 安全输出验证 | ❌ | P2 | 扩展 validate 校验器覆盖 AI 输出安全扫描 | 中 |
| C-01 上下文压缩提示 | ❌ | P2 | 在 AGENTS.md 中提示 AI 注意上下文压缩策略 | 小 |
| P-02 通配符路径匹配 | ❌ | P3 | 扩展 access 路径匹配支持 glob 通配符 | 小 |
| P-05 自动模式 | ❌ | P3 | 在 CI 脚本中增加 `--auto` 模式支持 | 小 |
| C-02 上下文可视化 | ❌ | P3 | 可选改进，平台特性 | 极小 |
| C-04 会话持久化 | ❌ | P3 | 平台特性，不一定要实现 | 不适用 |

## 4. 优化路线图

### Phase 1（P0，基础设施）

> 目标：AI 具备条件式规则加载和 Plan/Execute 分治能力，核心流程可打包为 Skill。

| 项 | 实践 | 描述 |
|----|------|------|
| 1 | R-01 | manifest path 升级为 glob 条件注入，rules 按路径自动匹配 |
| 2 | W-01 | 声明 plan-only context，减少 Safety 提示指令 |
| 3 | T-04 | 将 reconcile/revert/sync/git-push 打包为 Agent Skill |

**验收标准：**
- AI 操作不同路径时自动加载不同 rules 集
- 有独立的 Plan agent context 声明
- 至少有一个核心工作流可通过 `@skill-name` 触发

### Phase 2（P1，流程增强）

> 目标：AI 具备四阶段端到端工作流和自动质量门禁。

| 项 | 实践 | 描述 |
|----|------|------|
| 4 | R-02 | 建立多级规则优先级链（用户 > 项目 > 文件） |
| 5 | W-02 | WORKFLOW.md 增加 Research/Plan/Code/PR 阶段声明 |
| 6 | W-03 | 标准化 todo 跟踪格式到 WORKFLOW.md |
| 7 | C-03 | manifest context 映射到平台原生 Skills/Rules |
| 8 | Q-01 | 通过 Hooks 实现编辑后自动格式化/lint |

**验收标准：**
- 所有 WORKFLOW.md 包含四阶段声明
- 自动格式化在每次编辑后触发
- 多级规则优先级可被 AI 理解和遵循

### Phase 3（P2-P3，高阶能力）

> 目标：harness 具备 MCP 接口、Hooks、沙箱等企业级能力。

| 项 | 实践 | 描述 |
|----|------|------|
| 9 | T-01 | MCP server 暴露 validate / 路径查询等能力 |
| 10 | T-02 | harness 脚本引入 pre/post 钩子模式 |
| 11 | T-03 | 公共库暴露为 `.opencode/tools/` |
| 12 | P-04 | 沙箱隔离声明 |
| 13 | P-06 | 安全输出验证 |
| 14 | C-01 | 上下文压缩提示 |
| 15 | P-02 / P-05 | 通配符路径 / 自动模式 |
| 16 | C-02 | 上下文可视化（平台特性，可选） |

**验收标准：**
- 外部工具可通过 MCP 调用 harness 能力
- 所有脚本支持 pre/post 钩子
- 有沙箱隔离策略文档

## 5. 决策原则

新增能力时的评判标准，按优先级排列：

1. **维度覆盖检查**：是否与 6 个维度之一（规则/上下文/工作流/工具链/安全/质量）覆盖重合？若无，应优先纳入现有维度。
2. **阶段匹配**：是否匹配当前阶段的优先级？P0 项未完成时不启动 P1 项。
3. **冲突检测**：是否与现有实现冲突？若有冲突，先评估现有实现是否可以增强而非替换。
4. **AI 自主性**：是否可被 AI 自主触发而非人工维护？优先选择 AI 可自主发现和使用的机制。
5. **测试防护**：是否有对应的测试/校验？新增能力必须有配套测试，否则不进路线图。
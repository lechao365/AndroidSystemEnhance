# 8 项特有优秀实践极致补强设计

> 对应 `engineering/harness/reference/harness-optimization-blueprint.md` 第2章。
> 将蓝图描述的8项本项目特有优秀实践从"概念锁定"提升为"可执行、可校验、可测试"的生产级实现。

## 1. Manifest 声明式索引 + 任务准入

### 现状

`rules/manifest.yaml` 完全不存在。任务准入矩阵当前仅以纯 Markdown 表格嵌入 `config/README.md`，无法被机器解析。

### 设计方案

新建 `engineering/harness/rules/manifest.yaml`，实现 context + rules + access 三位一体声明式索引。

#### 文件结构

```yaml
version: 1

contexts:
  - id: workspace-source-modify
    match: "~/workspace/**"
    scope_category: source
    rules:
      - "rules/source-code-modify.md"
    workflow:
      - "workflows/lc-sync-code-to-patchs/"
    access: require_evidence
    require_plan: false

  - id: patchs-archive
    match: "patchs/**"
    scope_category: archive
    rules:
      - "rules/source-code-modify.md"
    workflow:
      - "workflows/lc-sync-code-to-patchs/"
    access: require_evidence
    require_plan: false

  - id: patchs-revert
    match: "patchs/**"
    scope_category: revert
    rules:
      - "rules/source-code-modify.md"
    workflow:
      - "workflows/lc-revert-code-from-patchs/"
    access: direct_edit
    require_plan: true
    require_confirmation: true
    require_evidence: true

  - id: doc-sync
    match: "patchs/**"
    scope_category: doc-sync
    rules:
      - "rules/doc-paths.md"
      - "rules/plantuml.md"
    workflow:
      - "workflows/lc-sync-patchs-to-doc/"
    access: require_plan
    require_plan: true
    require_confirmation: true
    require_evidence: true

  - id: git-push
    match: "**"
    scope_category: git
    rules: []
    workflow:
      - "workflows/lc-git-push-to-server/"
    access: require_confirmation
    require_confirmation: true

  - id: harness-script
    match: "engineering/harness/scripts/**"
    scope_category: harness
    rules:
      - "rules/script-observability.md"
    access: direct_edit
    require_evidence: true

  - id: harness-rules
    match: "engineering/harness/rules/**"
    scope_category: harness
    rules:
      - "engineering/harness/rules/README.md"
    access: require_plan
    require_plan: true

  - id: harness-config
    match: "engineering/harness/config/**"
    scope_category: harness
    rules:
      - "rules/path-management.md"
    access: direct_edit
    require_plan: false

  - id: harness-validator
    match: "engineering/harness/tests/**"
    scope_category: test
    rules:
      - "rules/script-observability.md"
    access: direct_edit
    require_evidence: true

  - id: docs
    match: "docs/**"
    scope_category: docs
    rules:
      - "rules/doc-paths.md"
      - "rules/plantuml.md"
    access: require_confirmation
    require_confirmation: true

access_levels:
  - level: direct_edit
    description: "允许直接编辑无需确认"
  - level: require_workflow
    description: "必须经过指定 workflow"
  - level: require_plan
    description: "必须先出实施计划"
  - level: require_confirmation
    description: "必须逐条确认"
  - level: require_evidence
    description: "必须留证据（manifest/baseline）"
```

#### 关键设计决策

- `match` 使用 glob 模式匹配
- `patchs/**` 有3个 context（archive/revert/doc-sync），由调用方显式指定 `scope_category` 区分
- access 5级线性递增：`direct_edit` → `require_workflow` → `require_plan` → `require_confirmation` → `require_evidence`

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `engineering/harness/rules/manifest.yaml` | 新建 | 声明式索引，8+ 个 context |
| `engineering/harness/scripts/validate_manifest.sh` | 新建 | Manifest 校验器 |
| `AGENTS.md` | 修改 | 加入 Manifest 准入查询指令 |

---

## 2. Access 五级控制

### 现状

完全依赖 manifest.yaml，当前不存在。`config/README.md` 准入矩阵有6列控制语义但非机器可解析。

### 设计方案

在 manifest.yaml 中明确定义五级 access（已在第1项中完成），同时配套：

1. **`manifest.yaml` 中的 `access_levels` 定义块** — 五级语义声明
2. **新增 `scripts/check_access.sh`** — CLI 工具查询准入判定
3. **AGENTS.md 引用** — 确保 AI 进入任务前先查询 manifest

#### check_access.sh 设计

```bash
# 用法
bash scripts/check_access.sh --path ~/workspace/aosp/foo --category source

# 输出（JSON 格式）
{
  "allowed": true,
  "access": "require_evidence",
  "rules": ["rules/source-code-modify.md"],
  "workflow": ["workflows/lc-sync-code-to-patchs/"],
  "require_plan": false,
  "require_confirmation": false,
  "require_evidence": true
}
```

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `engineering/harness/scripts/check_access.sh` | 新建 | 准入查询 CLI |
| `engineering/harness/tests/test_check_access.sh` | 新建 | 准入查询测试 |
| `AGENTS.md` | 修改 | Manifest 查询指令 |

---

## 3. 多语言统一路径工具

### 现状

完整实现（10/10）。shell/python/bat 三端齐全，`harness-paths.conf` 是单一事实源。

### 设计方案（4项增强）

#### 3.1 路径存在性校验函数

在 `harness_path_util.sh` 中新增：

```bash
# 检查 paths.conf 中所有 KEY 指向的目录是否存在
# 返回：ALL_EXIST 或 MISSING:KEY1,KEY2
harness_validate_paths() {
    local missing=()
    for key in LOG_DIR ARTIFACTS_DIR TEST_SANDBOX_DIR ...; do
        local val
        val=$(harness_path "$key")
        [ -d "$val" ] || missing+=("$key")
    done
    [ ${#missing[@]} -eq 0 ] && echo "ALL_EXIST" || echo "MISSING:${missing[*]}"
}
```

在 `harness_init` 中可选调用，通过 `--validate-paths` 开关控制。

#### 3.2 Python CLI 入口

在 `harness_path_util.py` 中新增：

```python
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "--resolve":
        key = sys.argv[2]
        print(harness_path(key))
```

#### 3.3 路径工具对称性测试

新增 `tests/test_harness_path_util.sh`，覆盖：
- shell/python/bat 三端查询同一 path KEY 结果一致
- 环境覆盖变量生效（`ENV_*` 前缀）
- 缺失 KEY 返回错误退出码
- `harness_repo_root` 在各种工作目录下的正确性

#### 3.4 路径配置热加载

```bash
# 运行时重新加载 paths.conf（无需重启脚本）
harness_reload_paths() {
    _H_CONFIG_PATHS=()
    while IFS='=' read -r key val; do
        _H_CONFIG_PATHS["$key"]="$val"
    done < "$_H_CONF_FILE"
}
```

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `lib/shell/harness_path_util.sh` | 修改 | 新增 `harness_validate_paths` + `harness_reload_paths` |
| `lib/python/harness_path_util.py` | 修改 | 新增 CLI 入口 |
| `lib/shell/harness_observability.sh` | 修改 | `harness_init` 支持 `--validate-paths` |
| `tests/test_harness_path_util.sh` | 新建 | 路径工具对称性测试 |

---

## 4. 基线晋升与回退

### 现状

存在 `baseline-status.yaml`（2条 promoted 记录）和 `baseline-evidence-template.yaml`，但缺失 archive/candidate 状态示例，缺少机器可读的状态校验。

### 设计方案（5项增强）

#### 4.1 baseline-status.yaml 补充 archive/candidate 示例

```yaml
baselines:
  - baseline_id: BL-20260626-01
    status: archive
    source_branch: rpi5-dev
    source_commit: deadbeef
    sync_manifest: "（待同步）"

  - baseline_id: BL-20260627-01
    status: candidate
    source_branch: rpi5-dev
    source_commit: cafebabe
    sync_manifest: "engineering/output/log/sync_code_to_patchs/artifacts/20260627-000000-manifest.yaml"
    build_result: PASS
    package_result: FAIL
```

#### 4.2 新增 baseline 校验器

`scripts/validate_baseline_status.sh`，校验：
- `baseline_id` 格式 `BL-YYYYMMDD-NN`
- `status` 值域 `archive|candidate|promoted`
- 三阶段字段完整性
- promoted 必须有 `approved_by` + `approved_at`
- 禁止同 id 跨阶段回退

#### 4.3 基线测试

`tests/test_baseline_workflow.sh`，覆盖：
- archive → candidate 字段补齐后状态前进
- candidate → promoted 全部字段填齐后晋升成功
- 缺失字段时被校验器拒绝

#### 4.4 证据模板增强

`baseline-evidence-template.yaml` 增加字段：
- `revert_count: 0` — 被回退次数
- `rollback_to: BL-YYYYMMDD-NN` — 回退目标记录

#### 4.5 AGENTS.md 基线使用指引

AI 在执行 revert 前必须先查 baseline-status.yaml 确认可回退。

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `config/baseline-status.yaml` | 修改 | 补充 archive/candidate 示例 |
| `config/baseline-evidence-template.yaml` | 修改 | 增加 revert_count/rollback_to 字段 |
| `scripts/validate_baseline_status.sh` | 新建 | 基线状态校验器 |
| `tests/test_baseline_workflow.sh` | 新建 | 基线晋升全流程测试 |
| `AGENTS.md` | 修改 | 基线使用指引 |

---

## 5. Observability 公共库

### 现状

`harness_observability.sh`（526行）功能全面，公共/私有 API 分离严格。

### 设计方案（5项增强）

#### 5.1 运行时性能指标采集

```bash
# 采集 CPU/内存/磁盘，写入结构化日志
harness_collect_metrics() {
    local cpu mem disk
    cpu=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}')
    mem=$(free -m | awk '/Mem:/ {print $3}')
    disk=$(df -h / | tail -1 | awk '{print $5}')
    _h_log_raw "cpu=$cpu mem=${mem}MB disk=$disk"
}

# 后台周期采集（默认 60s）
harness_start_metrics_watch() { ... }
harness_stop_metrics_watch() { ... }
```

#### 5.2 内置断言 API

将测试脚本中的自建断言下沉到公共库：

```bash
harness_assert_eq() { [ "$1" = "$2" ] || _h_assert_fail "$3" "$1" "!=" "$2"; }
harness_assert_file_exists() { [ -f "$1" ] || _h_assert_fail "$2" "file not found: $1"; }
harness_assert_grep() { grep -q "$1" "$2" || _h_assert_fail "$3" "pattern not found in $2"; }
harness_assert_exit_code() { [ "$?" -eq "$1" ] || _h_assert_fail "expected exit $1, got $?"; }
```

#### 5.3 新增 trace 日志级别

```bash
harness_trace() {
    [ "${HARNESS_TRACE:-0}" = "1" ] || return 0
    _h_log_raw "level=TRACE msg=$*"
}
```

#### 5.4 日志字段扩展

当前 `ts= level= step= script= msg=`，新增可选字段：
- `pid=$$`
- `duration=$(($(date +%s) - _H_START_TS))`
- `caller=${FUNCNAME[1]:--}`

#### 5.5 harness_report_no_upstream 增强

报错后输出诊断建议：`git remote -v`、`git branch -vv` 结果。

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `lib/shell/harness_observability.sh` | 修改 | 5项增强全部实现 |
| `tests/test_harness_observability.sh` | 修改 | 补全测试覆盖新增 API |

---

## 6. 工作流契约化

### 现状

5个工作流，每个有完整 WORKFLOW.md。质量优秀。

### 设计方案（5项增强）

#### 6.1 四阶段声明（Phase 2 W-02）

每个 WORKFLOW.md front matter 增加：

```yaml
stages:
  - research: "AI 分析 diff/上下文"
  - plan: "AI 生成实施计划，经用户确认"
  - code: "执行具体操作"
  - review: "验证结果并提交"
```

#### 6.2 新增 quick-fix 测试夹具

`fixtures/lc-quick-fix-issue/`，模拟 quick-fix 完整流程。

#### 6.3 TODO 跟踪章节（Phase 2 W-03）

每个 WORKFLOW.md 增加：

```markdown
## TODO 跟踪
- [ ] Step 1: 分析问题
- [ ] Step 2: 生成 plan
- [ ] Step 3: 用户确认
- [ ] Step 4: 执行
- [ ] Step 5: 验证
```

#### 6.4 工作流契约校验器

`scripts/validate_workflow_contracts.sh`，校验：
- stages 声明完整性
- TODO 格式一致性
- front matter 中 `name` + `description` + `stages` 必填

#### 6.5 退出码矩阵

每个 WORKFLOW.md 增加退出码表

| 退出码 | 含义 | 下一步 |
|--------|------|--------|
| 0 | 成功 | 正常继续 |
| 1 | 脚本逻辑错误 | 检查日志 |
| 3 | 环境缺失 | 安装依赖后重试 |

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `workflows/*/WORKFLOW.md`（5个） | 修改 | 加 stages/TODO/退出码表 |
| `scripts/validate_workflow_contracts.sh` | 新建 | 工作流契约校验器 |
| `tests/fixtures/lc-quick-fix-issue/` | 新建 | 测试夹具 |

---

## 7. 配置静态校验流水线

### 现状

3个独立校验器，各自职责明确，代码净化器是亮点。缺少全量校验入口。

### 设计方案（4项增强）

#### 7.1 全量校验聚合入口

`scripts/run_all_validations.sh`，顺序执行所有校验器，任一失败即中止：

```bash
VALIDATORS=(
  "validate_harness_scripts.sh"     # P0: 脚本合规
  "validate_harness_config.sh"      # P0: 配置合法性
  "validate_harness_docs.sh"        # P1: 文档一致性
  "validate_baseline_status.sh"     # NEW: 基线状态
  "validate_workflow_contracts.sh"  # NEW: 工作流契约
  "validate_manifest.sh"            # NEW: manifest 校验
)
```

#### 7.2 Manifest 校验器

`scripts/validate_manifest.sh`，校验：
- YAML 可解析
- `contexts[].id` 唯一
- `access` 值域限于 5 级
- `scope_category` 值域预定义
- `match` 非空 glob

#### 7.3 校验器自测

`tests/test_validators.sh`，验证校验器对有意的坏配置正确拒绝。

#### 7.4 validate_harness_config.sh 扩展

新增强校验项：baseline-status.yaml 状态字段校验 + 三阶段字段完整性校验。

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/run_all_validations.sh` | 新建 | 全量校验聚合入口 |
| `scripts/validate_manifest.sh` | 新建 | Manifest 校验器 |
| `scripts/validate_harness_config.sh` | 修改 | 扩展 baseline 校验 |
| `tests/test_validators.sh` | 新建 | 校验器自测 |

---

## 8. 测试框架 + 夹具

### 现状

4个测试脚本，3套夹具，自建轻量断言。缺失聚合运行器、部分 API 测试、部分工作流夹具。

### 设计方案（6项增强）

#### 8.1 测试聚合运行器

`tests/run_all_tests.sh`，按序执行所有 `test_*.sh`，输出 PASS/FAIL 汇总表。

#### 8.2 test_harness_observability.sh 补全

新增测试：
- `log_warn` / `log_error` 输出格式
- `harness_on_exit_add` 回调注册
- `harness_report_no_upstream` 报错输出
- `harness_assert_*` 系列
- `harness_trace` 在 `HARNESS_TRACE=1` 下的行为

#### 8.3 路径工具测试

`tests/test_harness_path_util.sh`，覆盖：
- 三端查询同一 KEY 结果一致
- 环境覆盖生效
- 缺失 KEY 返回错误
- `harness_repo_root` 正确性

#### 8.4 quick-fix 夹具

`fixtures/lc-quick-fix-issue/`

#### 8.5 doc-sync 夹具

`fixtures/lc-sync-patchs-to-doc/`

#### 8.6 README 回归矩阵

`tests/README.md` 增加测试回归矩阵表。

### 交付物

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/run_all_tests.sh` | 新建 | 测试聚合运行器 |
| `tests/test_harness_observability.sh` | 修改 | 补全 API 测试 |
| `tests/test_harness_path_util.sh` | 新建 | 路径工具对称性测试 |
| `tests/fixtures/lc-quick-fix-issue/` | 新建 | 测试夹具 |
| `tests/fixtures/lc-sync-patchs-to-doc/` | 新建 | 测试夹具 |
| `tests/README.md` | 修改 | 增加回归矩阵 |

---

## 实施顺序

按依赖关系分3批：

| 批次 | 项 | 依赖 |
|------|----|------|
| **Batch A** | P1 Manifest + P2 Access + P5 Observability + P3 Path Util | 无 |
| **Batch B** | P4 Baseline + P6 Workflow + P7 Validator | 依赖 Batch A（observability） |
| **Batch C** | P8 Test Framework | 依赖 Batch A+B |

---

## 文件汇总

| # | 操作 | 文件 |
|---|------|------|
| 新建 | 8 | `rules/manifest.yaml`, `scripts/check_access.sh`, `scripts/validate_manifest.sh`, `scripts/validate_baseline_status.sh`, `scripts/validate_workflow_contracts.sh`, `scripts/run_all_validations.sh`, `tests/run_all_tests.sh`, `tests/test_harness_path_util.sh`, `tests/test_baseline_workflow.sh`, `tests/test_check_access.sh`, `tests/test_validators.sh` |
| 修改 | 10+ | 5个 WORKFLOW.md, 3个 config 文件, 2个 lib 文件, AGENTS.md, test 文件, README 文件 |
# Scripts

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：harness 公共工程工具脚本集——编译打包、静态校验（文档 / 脚本 / 配置三类 validator）
- **职责边界**：做 harness 级独立脚本与校验器；不做 loop 专属入口（loop 入口在 `../../loop/scripts/`）
- **上下游依赖**：被 `AGENTS.md`、各 workflow、开发者手动调用；validator 依赖 `config/*.yaml`、`lib/`

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 脚本清单与已迁出文件 | 了解结构时 |
| [使用方式](#使用方式) | validator 调用示例与退出码语义 | 实际校验时 |
| [Windows .bat 脚本注意事项](#windows-bat-脚本注意事项) | .bat 换行符 / 编码 / 验证规则（**SSOT**） | 修改 .bat 文件时 🔖 |
| [关联资源](#关联资源) | 设计文档、规则、workflow、配置链接 | 深入理解时 |

## 目录说明

| 脚本 | 作用 | 调用方式 |
|------|------|---------|
| [`check_access.sh`](./check_access.sh) | Access 准入查询 CLI：给定路径+分类，输出 manifest 匹配的 access 级别/规则/workflow | `bash engineering/harness/scripts/check_access.sh --path <path> --category <category>` |
| [`mk_rpi5_full_image.sh`](./mk_rpi5_full_image.sh) | RPi5 AOSP 一键编译打包脚本（`-mode` 选构建范围） | `bash engineering/harness/scripts/mk_rpi5_full_image.sh` |
| [`run_all_validations.sh`](./run_all_validations.sh) | 全量校验聚合入口，顺序执行所有 validator | `bash engineering/harness/scripts/run_all_validations.sh` |
| [`validate_baseline_status.sh`](./validate_baseline_status.sh) | Baseline 状态校验（三阶段字段完整性、ID 格式、promoted 证据） | `bash engineering/harness/scripts/validate_baseline_status.sh` |
| [`validate_harness_docs.sh`](./validate_harness_docs.sh) | 文档 / 契约层静态校验（README 链接、文件清单、PlantUML 闭合、WORKFLOW front matter） | `bash engineering/harness/scripts/validate_harness_docs.sh` |
| [`validate_harness_scripts.sh`](./validate_harness_scripts.sh) | bash 脚本合规校验（bootstrap source、`harness_init`、裸 `exit` / `/tmp/` / 私有符号） | `bash engineering/harness/scripts/validate_harness_scripts.sh` |
| [`validate_harness_config.sh`](./validate_harness_config.sh) | 配置层校验（YAML 可解析性、字段合法性、命名规范） | `bash engineering/harness/scripts/validate_harness_config.sh` |
| [`validate_lcharness_layer_map.sh`](./validate_lcharness_layer_map.sh) | `LcHarness` Phase 1 层次映射校验（layer/kind/pack_type/path 唯一性与存在性） | `bash engineering/harness/scripts/validate_lcharness_layer_map.sh` |
| [`validate_manifest.sh`](./validate_manifest.sh) | Manifest.yaml 校验（context ID 唯一、access 值域、scope_category 合法性） | `bash engineering/harness/scripts/validate_manifest.sh` |
| [`validate_workflow_contracts.sh`](./validate_workflow_contracts.sh) | 工作流契约校验（front matter stages/TODO/退出码章节完整性） | `bash engineering/harness/scripts/validate_workflow_contracts.sh` |
| [`apply_preset_bugs.sh`](./apply_preset_bugs.sh) | 向 workspace 注入预设 bug，验证 AI 闭环修复能力（`--bug N` 选 bug，`--revert` 回滚） | `bash engineering/harness/scripts/apply_preset_bugs.sh --bug 1,2,3` |
| [`start-opencode-server.sh`](./start-opencode-server.sh) | 在 WSL2 内以 systemd user service 托管 `opencode web`，复用 `~/.config/opencode/server.env`，并在 Windows 宿主上自动配置 `tailscale serve`，输出手机可访问的 HTTPS WebUI URL | `bash engineering/harness/scripts/start-opencode-server.sh` |

**已迁出到 `../../loop/scripts/`**：`le.sh`、`le_runs_cleanup.sh`、`rp5_serial_helper.py`、`start_rp5_serial_host.bat` → 见 `../../loop/scripts/README.md`。

## 使用方式

### OpenCode WebUI 一键启动（WSL2 + Windows Tailscale）

前置条件：
- WSL2 已启用 `systemd`
- Windows 已安装并登录 Tailscale
- `~/.config/opencode/server.env` 已配置 `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD`

常用命令：

```bash
bash engineering/harness/scripts/start-opencode-server.sh
bash engineering/harness/scripts/start-opencode-server.sh --status-only
bash engineering/harness/scripts/start-opencode-server.sh --restart-serve-only
```

默认行为：
- WSL2 内生成/更新 `opencode-web.service`
- 以项目根为 `WorkingDirectory` 启动 `opencode web --hostname 127.0.0.1 --port 4096`
- Windows 侧执行 `tailscale serve --bg --https=443 http://localhost:4096`
- 输出 tailnet HTTPS URL，供手机浏览器访问 WebUI

### 静态校验器（validator）

harness 自身的文档 / 脚本 / 配置一致性静态校验入口，无副作用、只读扫描。

| 校验器 | 校验项 | 退出码语义 |
|--------|--------|-----------|
| `validate_harness_docs.sh` | README 导航链接存在性；各子目录 README 文件清单与实际目录一致性；`templates/*.md` 中 PlantUML `@startuml`/`@enduml` 配对闭合与花括号占位符；`workflows/*/WORKFLOW.md` front matter（含 `name`/`description`） | `0`=全绿 / `1`=有告警 / `3`=环境错误 |
| `validate_harness_scripts.sh` | `workflows/*/*.sh` 与 `scripts/*.sh` 是否 source `harness_bootstrap.sh`；是否调用 `harness_init`；是否出现裸 `exit` / 裸 `/tmp/` / 直接依赖 `_H_*`/`_h_*` 私有符号（公共库自身豁免） | 同上 |
| `validate_harness_config.sh` | `scope-mapping.yaml` / `doc-sync-mapping.yaml` 存在且可被 python3 解析；`version` 合法性；`priority` 为整数；`match` 非空；`scope` 命名规范；`mode` 值域；`routes[].docs` 项以 `docs/` 开头。依赖 `python3`（含 `yaml` 模块） | 同上 |

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
bash engineering/harness/scripts/validate_harness_scripts.sh
bash engineering/harness/scripts/validate_harness_config.sh
```

## Windows .bat 脚本注意事项

> **本节是 .bat 注意事项的单一事实源；`../../loop/scripts/README.md` 以链接形式引用本节。**

本项目中的 `.bat` 文件（`start_rp5_serial_host.bat`、`harness_path_util.bat` 等）有严格的格式要求，违反会导致乱码、解析失败或模块找不到。

### 1. 换行符必须为 CRLF（`\r\n`）

**这是最关键的一条**。Windows `cmd.exe` 要求批处理文件使用 CRLF 行尾。

**失败症状**（使用 LF 换行符时）：
- 输出中文乱码
- 命令解析错误
- `REM` 中文注释中的元字符被 CMD 执行
- 变量未正确设置，Python 找不到模块

**如何检测**：
```bash
file start_rp5_serial_host.bat
# 正常应显示 "CRLF line terminators"，否则为 LF-only
```

**如何修复**：

| 工具 | 命令 |
|------|------|
| Linux `unix2dos` | `unix2dos start_rp5_serial_host.bat` |
| VS Code | 右下角点击 `LF` → 切换为 `CRLF` |
| Git | `git config core.autocrlf true`（自动转换检出） |

### 2. 编码必须为纯 ASCII（禁止任何非 ASCII 字符）

> `.bat` 文件正文必须为纯 ASCII，所有注释、echo 输出一律使用英文。

**检查**：
```bash
# 应显示 "ASCII text"
file engineering/loop/scripts/start_rp5_serial_host.bat
```

**中文说明只能放在**：
- 配套的 `README.md`（Markdown，UTF-8 安全）
- 同目录的 `.md` 文档

### 3. 推荐在 CMD 中运行

建议直接在 CMD 中执行。

### 4. 修改后必须验证

每次修改 `.bat` 文件后，执行以下校验：
```bash
for f in engineering/loop/scripts/*.bat engineering/harness/lib/bat/*.bat; do python3 -c "import sys; d=open(sys.argv[1],'rb').read(); print(sys.argv[1], 'non_ascii=', sum(1 for b in d if b>127), 'lf_only=', d.count(b'\n')-d.count(b'\r\n'))" "$f"; done
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联规则 | `../rules/script-observability.md`（SCRIPT-001） | 改 harness 下 bash 脚本时加载 |
| 关联配置 | `../config/harness-paths.conf` | 编译路径 KEY：`ENV_KERNEL_WS` / `ENV_AOSP_WS` / `ENV_KERNEL_OUT` / `ENV_CLANG_BIN` / `ENV_WINDOWS_IMG_DIR` |
| 关联 workflow | `../workflows/` | validator 被 workflow 自检环节调用 |


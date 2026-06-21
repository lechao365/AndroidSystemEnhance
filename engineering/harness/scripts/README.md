# Scripts

独立一次性脚本——属于 harness 公共工程工具，而不是 loop engineering 专属入口。

## 文件说明
- [`mk_rpi5_full_image.sh`](./mk_rpi5_full_image.sh) — 树莓派 5 AOSP 一键编译打包脚本。
- [`validate_harness_docs.sh`](./validate_harness_docs.sh) — 文档/契约层静态校验。
- [`validate_harness_scripts.sh`](./validate_harness_scripts.sh) — bash 合规校验。
- [`validate_harness_config.sh`](./validate_harness_config.sh) — 配置层校验。

### 静态校验器（validator）

harness 自身的文档 / 脚本 / 配置一致性静态校验入口，无副作用、只读扫描，退出码 `0`=全绿、`1`=有告警、`3`=环境错误。

- [`validate_harness_docs.sh`](./validate_harness_docs.sh) — 文档/契约层校验：README 导航链接存在性、各子目录 README 文件清单与实际目录一致性、`templates/*.md` 中 PlantUML `@startuml`/`@enduml` 配对闭合与花括号占位符、`workflows/*/WORKFLOW.md` front matter（含 `name`/`description`）。
  - 调用：`bash engineering/harness/scripts/validate_harness_docs.sh`
- [`validate_harness_scripts.sh`](./validate_harness_scripts.sh) — bash 脚本合规校验：`workflows/*/*.sh` 与 `scripts/*.sh` 是否 source `harness_bootstrap.sh`、是否调用 `harness_init`、是否出现裸 `exit` / 裸 `/tmp/` / 直接依赖 `_H_*`/`_h_*` 私有符号（公共库自身豁免）。
  - 调用：`bash engineering/harness/scripts/validate_harness_scripts.sh`
- [`validate_harness_config.sh`](./validate_harness_config.sh) — 配置层校验：`scope-mapping.yaml` / `doc-sync-mapping.yaml` 存在且可被 python3 解析、`version` 合法性、`priority` 为整数、`match` 非空、`scope` 命名规范、`mode` 值域、`routes[].docs` 项以 `docs/` 开头。依赖 `python3`（含 `yaml` 模块）。
  - 调用：`bash engineering/harness/scripts/validate_harness_config.sh`

## 已迁出到 `engineering/loop/scripts/`
- `le.sh`
- `le_runs_cleanup.sh`
- `rp5_serial_helper.py`
- `start_rp5_serial_host.bat`

## Windows .bat 脚本注意事项

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
python3 -c "print(sum(1 for b in open('engineering/loop/scripts/start_rp5_serial_host.bat','rb').read() if b>127))"
python3 -c "d=open('engineering/loop/scripts/start_rp5_serial_host.bat','rb').read();print(d.count(b'\n')-d.count(b'\r\n'))"
```

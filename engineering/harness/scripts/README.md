# Scripts

独立一次性脚本——不属于任何工作流闭环，通常是手动触发的构建或运维工具。

## 文件说明

- [`mk_rpi5_full_image.sh`](./mk_rpi5_full_image.sh) — 树莓派 5 AOSP 一键编译打包脚本。通过 `-mode` 参数选择构建范围（全量 / 仅打包 / 仅内核 / 仅 vendor / 仅 system），最终生成可刷写 SD 卡的 `.img`。

### 静态校验器（validator）

harness 自身的文档 / 脚本 / 配置一致性静态校验入口，无副作用、只读扫描，退出码 `0`=全绿、`1`=有告警、`3`=环境错误。

- [`validate_harness_docs.sh`](./validate_harness_docs.sh) — 文档/契约层校验：README 导航链接存在性、各子目录 README 文件清单与实际目录一致性、`templates/*.md` 中 PlantUML `@startuml`/`@enduml` 配对闭合与花括号占位符、`workflows/*/WORKFLOW.md` front matter（含 `name`/`description`）。
  - 调用：`bash engineering/harness/scripts/validate_harness_docs.sh`
- [`validate_harness_scripts.sh`](./validate_harness_scripts.sh) — bash 脚本合规校验：`workflows/*/*.sh` 与 `scripts/*.sh` 是否 source `harness_bootstrap.sh`、是否调用 `harness_init`、是否出现裸 `exit` / 裸 `/tmp/` / 直接依赖 `_H_*`/`_h_*` 私有符号（公共库自身豁免）。
  - 调用：`bash engineering/harness/scripts/validate_harness_scripts.sh`
- [`validate_harness_config.sh`](./validate_harness_config.sh) — 配置层校验：`scope-mapping.yaml` / `doc-sync-mapping.yaml` 存在且可被 python3 解析、`version` 合法性、`priority` 为整数、`match` 非空、`scope` 命名规范、`mode` 值域、`routes[].docs` 项以 `docs/` 开头。依赖 `python3`（含 `yaml` 模块）。
  - 调用：`bash engineering/harness/scripts/validate_harness_config.sh`

## 约定

- 脚本同样遵守 [script-observability.md](../rules/script-observability.md) 规范（source 公共库、结构化日志）。
- 与 `workflows/` 的区别：scripts 是单脚本工具，无多步确认闭环；workflows 是脚本 + AI 交互的完整流程。

---

## le.sh

**位置**：[`le.sh`](./le.sh)

可通过 opencode slash command `/le` 触发（AI 主导闭环编排），或直接 bash 调用。

Loop Engineering v2 的 CLI 入口，底层调用 `loop_core.cli`。

**依赖**：bash + Python 3 + harness 环境。通常通过 WSL2 或 Linux 执行。

**用法**：

```bash
# fixture 模式（离线回放）
bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture <jsonl路径> \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>

# live 模式（需要先启动 Windows Host）
bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --host 127.0.0.1 --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>
```

**注意**：`le.sh` 通过 `harness_bootstrap.sh` 自动加载统一路径配置和维测框架，无需手动设置 `PYTHONPATH`。

---

## start_rp5_serial_host.bat

**位置**：[`start_rp5_serial_host.bat`](./start_rp5_serial_host.bat)

Windows 前台启动 rp5-serial Host，独占物理串口，监听 TCP 供 WSL2 Client 连接。

**依赖**：
- Windows 已安装 Python 3 并注册到 PATH（`python` 命令可用）
- `pyserial` 已安装（`pip install pyserial`）
- 物理串口设备已连接

**用法**（CMD 中运行）：

```bat
REM 默认参数：COM5 / 115200 / 9700
engineering\harness\scripts\start_rp5_serial_host.bat

REM 自定义 COM 口
engineering\harness\scripts\start_rp5_serial_host.bat COM3

REM 全参数自定义
engineering\harness\scripts\start_rp5_serial_host.bat COM3 9600 9800
```

停止：`Ctrl-C`。

---

## Windows .bat 脚本注意事项

本项目中的 `.bat` 文件（`start_rp5_serial_host.bat`、`harness_path_util.bat` 等）有严格的格式要求，违反会导致乱码、解析失败或模块找不到。

### 1. 换行符必须为 CRLF（`\r\n`）

**这是最关键的一条**。Windows `cmd.exe` 要求批处理文件使用 CRLF 行尾。

**失败症状**（使用 LF 换行符时）：
- 输出中文乱码（`浣跨敤榛樿鍙傛暟`、`淇濇寔鐩稿锛堝啋鍙峰垎闅旓紝渚?_h_py_loop` 等）
- 命令解析错误（`'KEY' 不是内部或外部命令`、`'TE' 不是内部或外部命令`、`'ial_host.bat'` 等）
- `REM` 中文注释中的元字符被 CMD 执行（`<KEY>`、`&`、`(...)` 等）
- 变量未正确设置，Python 找不到模块（`ModuleNotFoundError: No module named 'rp5_serial'`）

**如何检测**：
```bash
# Linux / WSL
file start_rp5_serial_host.bat
# 正常应显示 "CRLF line terminators"，否则为 LF-only

# 或 hex 查看前几字节
xxd start_rp5_serial_host.bat | head -5
# CRLF = 0d 0a，LF = 0a
```

**如何修复**：

| 工具 | 命令 |
|------|------|
| Linux `unix2dos` | `unix2dos start_rp5_serial_host.bat` |
| VS Code | 右下角点击 `LF` → 切换为 `CRLF` |
| Git | `git config core.autocrlf true`（自动转换检出） |

### 2. 编码必须为纯 ASCII（禁止任何非 ASCII 字符）

> **历史教训**：原规则要求 UTF-8 without BOM，但实测 `.bat` 中的中文（UTF-8）在 GBK 代码页（936）系统上会乱码，且 `REM` 注释**不抑制** CMD 元字符（`<` `>` `&` `|` `(` `)`）解析，导致：
> - `REM ...HARNESS_PATH_<KEY>...` → `<KEY` 被当作输入重定向 → `'KEY' 不是内部或外部命令`
> - `REM ...endlocal & (...)...` → `&` 被当作命令分隔符
> - `REM ...保持相对（冒号分隔，供 _h_py_loop 拆分）` → 乱码字节被当作命令执行
>
> 因此 **`.bat` 文件正文必须为纯 ASCII**，所有注释、echo 输出一律使用英文。

**检查**：
```bash
# 应显示 "ASCII text"（而非 "UTF-8 text"）
file engineering/harness/lib/bat/harness_path_util.bat

# 非法字节计数应为 0
python3 -c "print(sum(1 for b in open('file.bat','rb').read() if b>127))"
```

**中文说明只能放在**：
- 配套的 `README.md`（Markdown，UTF-8 安全）
- 同目录的 `.md` 文档
- `REM` 引用的外部规则文件（`.md`）

### 3. 推荐在 CMD 中运行

虽然 PowerShell 中可以直接运行 `.bat`（委托给 CMD），但编码和转义行为可能与原生 CMD 有细微差异。建议直接在 CMD 中执行。

### 4. 修改后必须验证

每次修改 `.bat` 文件后，执行以下校验：

```bash
# 1. 纯 ASCII（非 ASCII 字节数应为 0）
python3 -c "print(sum(1 for b in open('file.bat','rb').read() if b>127))"

# 2. CRLF 行尾（LF-only 行数应为 0）
python3 -c "d=open('file.bat','rb').read();print(d.count(b'\n')-d.count(b'\r\n'))"
```

校验通过后，在 Windows CMD 中运行一次，确认：
- 无乱码输出
- Python 模块正常加载
- 功能正常

---

> **关联规则**：`engineering/harness/rules/path-management.md`（PATH-001）
> **关联工具**：`engineering/harness/lib/bat/harness_path_util.bat`

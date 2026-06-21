# Loop Scripts

loop engineering 专属脚本入口。

## 文件说明
- `le.sh`：Loop Engineering CLI wrapper
- `le_runs_cleanup.sh`：LE runs 生命周期清理脚本
- `rp5_serial_helper.py`：供 loop host case / workflow 使用的串口辅助工具
- `start_rp5_serial_host.bat`：Windows 端 rp5-serial host daemon 启动器

## 依赖边界
- 允许依赖 `engineering/harness/lib/` 的公共 bootstrap / path / observability 能力
- 禁止把 loop-specific 脚本重新放回 `engineering/harness/scripts/`

---

## le.sh

**位置**：[`le.sh`](./le.sh)

可通过 opencode slash command `/le` 触发（AI 主导闭环编排），或直接 bash 调用。

Loop Engineering v2 的 CLI 入口，底层调用 `loop_core.cli`。

**依赖**：bash + Python 3 + harness 环境。通常通过 WSL2 或 Linux 执行。

**用法**：

```bash
# fixture 模式（离线回放）
bash engineering/loop/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture <jsonl路径> \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>

# live 模式（需要先启动 Windows Host）
bash engineering/loop/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --host 127.0.0.1 --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir <输出目录>
```

**注意**：`le.sh` 通过 `harness_bootstrap.sh` 自动加载统一路径配置和维测框架，无需手动设置 `PYTHONPATH`。

---

## le_runs_cleanup.sh

**位置**：[`le_runs_cleanup.sh`](./le_runs_cleanup.sh)

LE 框架 runs/ 产物自动清理脚本，由 `le.sh` 在每次运行结束时自动调用，也可手动触发。

**规则**：
- 仅清理 `runs/` 下**子目录**（run-id 目录），散文件（如 `probe-reboot.log`）保留不动
- 按目录 mtime 降序，保留最新 N 份（默认 20），删除其余
- 保留份数：`--keep N` > 环境变量 `LE_RUNS_KEEP` > 默认 20

**用法**：

```bash
# 默认（保留 20 份，环境变量 LE_RUNS_KEEP 可覆盖）
bash engineering/loop/scripts/le_runs_cleanup.sh

# 指定保留份数
bash engineering/loop/scripts/le_runs_cleanup.sh --keep 10

# 试运行，仅打印不删除
bash engineering/loop/scripts/le_runs_cleanup.sh --keep 20 --dry-run
```

**退出码**：`0`=已清理；`1`=部分删除失败；`3`=参数错误；`4`=无操作（含 `--dry-run`、未超份数、目录不存在）。

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
engineering\loop\scripts\start_rp5_serial_host.bat

REM 自定义 COM 口
engineering\loop\scripts\start_rp5_serial_host.bat COM3

REM 全参数自定义
engineering\loop\scripts\start_rp5_serial_host.bat COM3 9600 9800
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

# Lib

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：公共库，为 `engineering/` 下所有脚本（shell / python / bat）提供统一的路径解析、日志、结构化 step、错误捕获、产物归档能力
- **职责边界**：做公共基础设施函数；不做业务逻辑、不做 workflow 编排
- **上下游依赖**：被 `scripts/*.sh`、`workflows/*/*.sh`、`loop/scripts/*.sh` 通过 `harness_bootstrap.sh` 统一加载；依赖 `config/harness-paths.conf`（路径数据源）

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | lib 做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | shell / python / bat 三种语言的加载示例 | 写脚本时 |
| [公共 API 速查](#公共-api-速查) 🔖 | 路径 / 日志 / 状态 / 产物 / 错误 捕获 API | 改 / 写 harness 脚本时 |
| [关联资源](#关联资源) | 规则、配置链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 关键入口/被谁引用 |
|------|------|------------------|
| [`shell/harness_path_util.sh`](./shell/harness_path_util.sh) | 统一路径工具（REPO_ROOT 定位 + `paths.conf` 加载） | 被 bootstrap source；业务脚本可直接 source |
| [`shell/harness_bootstrap.sh`](./shell/harness_bootstrap.sh) | bootstrap 入口（source path_util + observability） | 业务脚本统一入口 |
| [`shell/harness_observability.sh`](./shell/harness_observability.sh) | 维测公共库（日志 / step / artifact / tmp / upstream） | 被 bootstrap source |
| [`python/harness_path_util.py`](./python/harness_path_util.py) | Python 版路径工具 | `from harness_path_util import path, ensure_dir` |
| [`bat/harness_path_util.bat`](./bat/harness_path_util.bat) | bat 版路径工具（load-and-set 模式） | `call ... harness_path_util.bat` |

## 使用方式

### shell（统一入口）

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"   # 自动定位 REPO_ROOT + source observability
harness_init "<script-name>"
```

source 后即可调用 `log_*` / `step_*` / `on_err` / `harness_status_emit` 等全部公共 API。

### shell（仅需路径能力）

```bash
source "$SCRIPT_DIR/../../lib/shell/harness_path_util.sh"
LOG_DIR=$(harness_path LOG_DIR)
```

### python

将 `lib/python` 加入 PYTHONPATH 后 import：

```python
from harness_path_util import path, ensure_dir
log_dir = path("HOST_LOG_DIR")
ensure_dir("HOST_LOG_DIR")
```

### bat

```bat
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
call "%SCRIPT_DIR%\..\..\lib\bat\harness_path_util.bat"
echo %REPO_ROOT%
echo %HARNESS_PATH_HOST_LOG_DIR%
echo %HARNESS_PATH_PYTHONPATH%
```

## 公共 API 速查

### 路径类

| 函数 / 变量 | 作用 | 所属文件 |
|------------|------|---------|
| `harness_repo_root` | 输出 REPO_ROOT 绝对路径 | shell/harness_path_util.sh |
| `harness_path <KEY>` | 输出 `harness-paths.conf` 中 KEY 对应的绝对路径 | shell/harness_path_util.sh |
| `harness_env_path <KEY>` | 输出环境可覆盖路径（先查 ENV，再查 config 默认值） | shell/harness_path_util.sh |
| `harness_pythonpath` | 输出拼好的 PYTHONPATH 字符串（绝对路径，冒号分隔） | shell/harness_path_util.sh |
| `path(key)` | 返回 KEY 对应的绝对路径（`Path`） | python/harness_path_util.py |
| `env_path(key)` | 返回环境可覆盖路径（`str`） | python/harness_path_util.py |
| `pythonpath()` | 返回 Python 包根绝对路径列表 | python/harness_path_util.py |
| `ensure_dir(key)` | `path(key)` + `mkdir(parents=True, exist_ok=True)` | python/harness_path_util.py |
| `HARNESS_PATH_<KEY>` / `REPO_ROOT` | load-and-set 变量（call 后全部可用） | bat/harness_path_util.bat |

### 日志 / 步骤类

| 函数 | 作用 | 所属文件 |
|------|------|---------|
| `log_info` / `log_warn` / `log_error` | 双格式日志（终端彩色 + 日志文件结构化） | shell/harness_observability.sh |
| `log_result "<title>" "k=v" ...` | 结构化结果记录 | shell/harness_observability.sh |
| `step_begin "<title>"` / `step_end [rc]` | 结构化 step（带耗时） | shell/harness_observability.sh |
| `harness_status_emit <OK\|MISS\|SKIP\|STALE\|PRUNE> <label> [msg]` | 逐文件状态输出 | shell/harness_observability.sh |

### 初始化 / 退出 / 状态 / 产物类

| 函数 | 作用 | 所属文件 |
|------|------|---------|
| `harness_init [--with-errexit] "<script-name>"` | 初始化（建日志目录、注册 trap） | shell/harness_observability.sh |
| `harness_exit [code]` | 收尾退出 | shell/harness_observability.sh |
| `harness_log_file` / `harness_artifacts_dir` | 路径查询 | shell/harness_observability.sh |
| `harness_now_iso` / `harness_started_at_epoch` | 时间 API | shell/harness_observability.sh |
| `harness_on_exit_add "<cmd>"` | 注册 EXIT 回调（替代手写 trap） | shell/harness_observability.sh |
| `harness_tmp_file <name>` / `harness_tmp_dir <name>` | 临时文件/目录（落入 artifacts，参与轮转） | shell/harness_observability.sh |
| `artifact_register <src> <name>` | 中间产物归档 | shell/harness_observability.sh |

### 错误捕获 / upstream 基线类

| 函数 | 作用 | 所属文件 |
|------|------|---------|
| `on_err [--continue] [--exit-code N] <lineno> <cmd> <rc>` | 错误现场捕获（模式 A 手动 / 模式 B trap 自动） | shell/harness_observability.sh |
| `harness_git_current_branch` | 当前分支名（detached HEAD 返回空串） | shell/harness_observability.sh |
| `harness_git_upstream_ref` | upstream ref（如 `origin/main`），无则空 | shell/harness_observability.sh |
| `harness_find_upstream_base` | merge-base HEAD `<upstream-ref>`，无 upstream 返回空 | shell/harness_observability.sh |
| `harness_report_no_upstream "<ctx>"` | upstream 缺失时的统一错误报告 | shell/harness_observability.sh |

> **API 边界**：业务脚本只能使用不带下划线前缀的公共 API；`_H_*` / `_h_*` 为库内部私有，禁止直接依赖。
>
> observability API 详解见 [`../rules/script-observability.md`](../rules/script-observability.md)；路径 API 详解见 [`../rules/path-management.md`](../rules/path-management.md)（PATH-001）。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联规则 | [`../rules/script-observability.md`](../rules/script-observability.md) | observability API 详解 |
| 关联规则 | [`../rules/path-management.md`](../rules/path-management.md)（PATH-001） | 路径 API 详解 |
| 关联配置 | [`../config/harness-paths.conf`](../config/harness-paths.conf) | 路径 KEY 单一事实源 |

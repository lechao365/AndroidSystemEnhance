# 路径管理规则

> **规则 ID**：`PATH-001`
> - `engineering/` 下所有脚本（shell / python / bat）禁止硬编码工程内路径，必须通过统一路径工具获取。
> - 路径配置的单一事实源为 `engineering/harness/config/harness-paths.conf`，三方工具（shell / python / bat）均从此文件加载。

## 1. 适用范围

- **适用对象**：`engineering/` 下所有脚本（harness/workflows/、harness/scripts/、loop/、tests/、Windows .bat）。
- **加载时机**：新增脚本或改动现有脚本的路径引用前，必须先加载本规则。

## 2. 强制要求（MUST）

1. **MUST** 通过统一路径工具获取工程内路径，禁止硬编码 `engineering/output/...`、`patchs/...`、`engineering/harness/...` 等字面值。
2. **MUST** 新增路径时，先在 `config/harness-paths.conf` 中定义 KEY，再通过工具 API 引用。
3. **MUST** 环境可覆盖路径（如 workspace 路径）使用 `ENV_*` 前缀的 KEY，保留 `${ENV_VAR:-default}` 覆盖语义。
4. **MUST** 三方工具的选择：
   - shell 脚本：source `lib/shell/harness_path_util.sh`（或通过 bootstrap 间接 source）
   - python 脚本：import `harness_path_util`（需将 `lib/python` 加入 PYTHONPATH）
   - bat 脚本：call `lib/bat/harness_path_util.bat`

## 3. 禁止行为（MUST NOT）

1. **MUST NOT** 在脚本中硬编码本文件已定义的路径字面值。
2. **MUST NOT** 重复实现 REPO_ROOT 查找逻辑（统一由 path_util 基于 AGENTS.md 锚点完成）。
3. **MUST NOT** 在 shell/python/bat 各自维护独立的路径常量文件。
4. **MUST NOT** 使用相对路径 `output/host-log` 这类依赖 CWD 的写法。

## 4. 路径工具 API

### Shell（`lib/shell/harness_path_util.sh`）

```bash
source "$SCRIPT_DIR/../../lib/shell/harness_path_util.sh"
REPO_ROOT=$(harness_repo_root)           # REPO_ROOT 绝对路径
LOG_DIR=$(harness_path LOG_DIR)          # paths.conf 中 KEY 对应的绝对路径
KERNEL_WS=$(harness_env_path ENV_KERNEL_WS)  # 环境可覆盖路径
PYTHONPATH=$(harness_pythonpath)         # 拼好的 PYTHONPATH
```

### Python（`lib/python/harness_path_util.py`）

```python
from harness_path_util import path, env_path, ensure_dir, repo_root
log_dir = path("HOST_LOG_DIR")           # -> Path 对象（绝对路径）
ensure_dir("HOST_LOG_DIR")               # path() + mkdir
kernel_ws = env_path("ENV_KERNEL_WS")    # -> str
```

### Bat（`lib/bat/harness_path_util.bat`）

```bat
call "%SCRIPT_DIR%\..\..\harness\lib\bat\harness_path_util.bat"
REM 加载后变量可用:
REM   %REPO_ROOT%
REM   %HARNESS_PATH_HOST_LOG_DIR%
REM   %HARNESS_PATH_PYTHONPATH%
```

## 5. 配置文件格式（`config/harness-paths.conf`）

```bash
# 相对路径（基于 REPO_ROOT 解析）
LOG_DIR="engineering/output/log"
HOST_LOG_DIR="engineering/output/host-log"

# 环境可覆盖路径（实际值 = ${ENV_VAR:-default}）
ENV_KERNEL_WS="$HOME/workspace/rpi5-kernel-build/common"

# 多值路径（冒号分隔）
PYTHON_PATH_ROOTS="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python"
```

## 6. 目录调整流程

当工程目录结构变更时，**仅修改 `config/harness-paths.conf`**，无需改动脚本：
1. 在 paths.conf 中更新对应 KEY 的值
2. 运行 `validate_harness_scripts.sh` 验证
3. 冒烟测试受影响的脚本

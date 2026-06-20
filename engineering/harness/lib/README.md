# Lib

公共库——为 `engineering/` 下所有脚本（shell / python / bat）提供统一的路径解析、日志、结构化 step、错误捕获、产物归档能力。

## 目录结构

```
lib/
├── shell/                      # shell 公共能力
│   ├── harness_path_util.sh    # 统一路径工具（REPO_ROOT 定位 + paths.conf 加载）
│   ├── harness_bootstrap.sh    # bootstrap 入口（source path_util + observability）
│   └── harness_observability.sh # 维测公共库（日志/step/artifact/tmp/upstream）
├── python/                     # python 公共能力
│   └── harness_path_util.py    # 统一路径工具（Python 版）
├── bat/                        # Windows 批处理公共能力
│   └── harness_path_util.bat   # 统一路径工具（bat 版）
└── README.md                   # 本文件
```

## 路径配置

所有路径的单一事实源: [`config/harness-paths.conf`](../config/harness-paths.conf)
路径管理规则: [`rules/path-management.md`](../rules/path-management.md) (PATH-001)

## 使用约定

### shell 脚本

所有 harness 脚本通过 `lib/shell/harness_bootstrap.sh` 统一入口加载（自动 source path_util + observability）：
```bash
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"
```
仅需路径能力的脚本可直接 source path_util:
```bash
source "$SCRIPT_DIR/../../lib/shell/harness_path_util.sh"
```

### python 脚本

将 `lib/python` 加入 PYTHONPATH 后 import:
```python
from harness_path_util import path, ensure_dir
```

### bat 脚本

```bat
call "%SCRIPT_DIR%\..\..\harness\lib\bat\harness_path_util.bat"
```

## 公共 API 速查

详见 [rules/script-observability.md](../rules/script-observability.md)（observability API）和 [rules/path-management.md](../rules/path-management.md)（路径 API）。

业务脚本只能使用不带下划线前缀的公共 API；`_H_*` / `_h_*` 为库内部私有，禁止直接依赖。

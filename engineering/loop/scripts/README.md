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

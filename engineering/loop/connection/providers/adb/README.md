# adb Provider

为 Loop Engineering 提供 `transport=adb` live transport。

## 范围
- adb connect / disconnect
- adb shell 命令执行（带 exit code 解析）
- adb root / su0 两种提权策略
- adb pull 文件拉取
- adb logcat 多 buffer 采集
- adb reboot + wait-for-device
- adb runtime context（endpoint / recent commands / reconnect count）

## Python 包
- `python/loop_adb/client.py`：adb 子进程封装
- `python/loop_adb/transport.py`：BaseTransport 适配层

## 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/adb/python" \
python3 -m pytest engineering/loop/connection/providers/adb/python/tests/ -v
```

"""rp5_serial_helper 单元测试（device-ip 过滤逻辑）。"""
import sys
from pathlib import Path

# scripts 目录不在 PYTHONPATH，显式加入以 import helper
# tests→python→rp5-serial→providers→connection→loop，scripts 在 loop 下
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[5] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import rp5_serial_helper  # noqa: E402
import rp5_serial.client.automation as _automation_mod  # noqa: E402


class _FakeAutomationClient:
    """模拟 AutomationClient：返回预设的串口行。"""

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def acquire_writer(self):
        return True

    def send_line(self, cmd):
        pass

    def read_until_timeout(self, t):
        return self._lines


def test_device_ip_excludes_linklocal_169_254(monkeypatch, capsys):
    """169.254.x.x（DHCP 失败 link-local）必须被排除，返回有效 IP。

    回归 P1-5：原过滤仅排除 127.x/0.0.0.0，漏 169.254，DHCP 失败时
    返回无效 link-local 地址导致 adb 连接失败。
    """
    fake = _FakeAutomationClient(["inet 169.254.1.5", "inet 192.168.1.100"])
    monkeypatch.setattr(_automation_mod, "AutomationClient", lambda *a, **kw: fake)
    rc = rp5_serial_helper.get_device_ip("127.0.0.1", 9700)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "192.168.1.100"


def test_device_ip_only_linklocal_returns_error(monkeypatch, capsys):
    """仅有 169.254 link-local 时应返回 exit 1（找不到有效 IP）。"""
    fake = _FakeAutomationClient(["inet 169.254.3.7"])
    monkeypatch.setattr(_automation_mod, "AutomationClient", lambda *a, **kw: fake)
    rc = rp5_serial_helper.get_device_ip("127.0.0.1", 9700)
    assert rc == 1
    assert "NO_IP_FOUND" in capsys.readouterr().err


def test_device_ip_excludes_loopback(monkeypatch, capsys):
    """127.x loopback 仍被排除（不回归既有过滤）。"""
    fake = _FakeAutomationClient(["inet 127.0.0.1", "inet 10.0.0.5"])
    monkeypatch.setattr(_automation_mod, "AutomationClient", lambda *a, **kw: fake)
    rc = rp5_serial_helper.get_device_ip("127.0.0.1", 9700)
    assert rc == 0
    assert capsys.readouterr().out.strip() == "10.0.0.5"

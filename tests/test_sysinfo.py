from types import SimpleNamespace
from unittest.mock import patch

from dragonfly.hub.sysinfo import get_system_stats


def test_system_stats_include_io_counters(tmp_path):
    disk = SimpleNamespace(read_bytes=1000, write_bytes=2000)
    network = SimpleNamespace(bytes_recv=3000, bytes_sent=4000)
    with patch("dragonfly.hub.sysinfo.psutil.disk_io_counters", return_value=disk), \
         patch("dragonfly.hub.sysinfo.psutil.net_io_counters", return_value=network), \
         patch("dragonfly.hub.sysinfo.time.monotonic", return_value=42):
        stats = get_system_stats(str(tmp_path))
    assert stats["io_timestamp"] == 42
    assert (stats["disk_read_bytes"], stats["disk_write_bytes"]) == (1000, 2000)
    assert (stats["network_received_bytes"], stats["network_sent_bytes"]) == (3000, 4000)


def test_system_stats_handle_unavailable_io_counters(tmp_path):
    with patch("dragonfly.hub.sysinfo.psutil.disk_io_counters", side_effect=OSError()), \
         patch("dragonfly.hub.sysinfo.psutil.net_io_counters", return_value=None):
        stats = get_system_stats(str(tmp_path))
    assert stats["disk_read_bytes"] is None
    assert stats["disk_write_bytes"] is None
    assert stats["network_received_bytes"] is None
    assert stats["network_sent_bytes"] is None
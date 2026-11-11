import threading
from unittest.mock import MagicMock, patch

import pytest

from dragonfly.hub.config import CameraConfig, HubConfig, StorageConfig
from dragonfly.hub.registry import DeviceRegistry
from dragonfly.watchdog.alerts import AlertManager, Ntfy, main
from dragonfly.watchdog.monitor import Watchdog


def test_ntfy_is_disabled_without_configuration(monkeypatch):
    for name in ("DRAGONFLY_NTFY_URL", "DRAGONFLY_NTFY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert Ntfy.from_environment() is None
    monkeypatch.setenv("DRAGONFLY_NTFY_TOKEN", "token")
    with pytest.raises(ValueError):
        Ntfy.from_environment()


def test_ntfy_sends_authenticated_request():
    sender = Ntfy("https://ntfy.sh/example-topic", "secret")
    response = MagicMock()
    response.__enter__.return_value.status = 201
    with patch("dragonfly.watchdog.alerts.urlopen", return_value=response) as urlopen:
        sender.send("Dragonfly: camera offline")
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://ntfy.sh/example-topic"
    assert request.get_header("Authorization") == "Bearer secret"
    assert request.get_header("Priority") == "high"
    assert request.data == b"Dragonfly: camera offline"
    assert urlopen.call_args.kwargs["timeout"] == 10


@pytest.mark.parametrize("url", ["http://ntfy.sh/topic", "https://ntfy.sh", "https://user:pass@ntfy.sh/topic"])
def test_ntfy_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        Ntfy(url)


def test_alerts_debounce_retry_and_reset():
    sender = MagicMock()
    alerts = AlertManager(sender, delay_s=30, retry_s=60)
    with patch("dragonfly.watchdog.alerts.time.monotonic") as clock:
        clock.return_value = 100
        alerts.observe("camera:OW", True, "camera offline")
        alerts._process()
        sender.send.assert_not_called()
        clock.return_value = 130
        sender.send.side_effect = [OSError(), None, None]
        alerts._process()
        assert sender.send.call_count == 1
        clock.return_value = 189
        alerts._process()
        assert sender.send.call_count == 1
        clock.return_value = 190
        alerts._process()
        alerts._process()
        assert sender.send.call_count == 2
        alerts.observe("camera:OW", False, "camera offline")
        alerts.observe("camera:OW", True, "camera offline")
        clock.return_value = 220
        alerts._process()
        assert sender.send.call_count == 3


def test_watchdog_observes_each_failure(tmp_path):
    config = HubConfig(
        storage=StorageConfig(recordings_path=str(tmp_path), mount_point=None),
        cameras=[CameraConfig(id="OW", name="Outdoor West", rtsp_url="rtsp://example/", record=True)],
    )
    alerts = MagicMock()
    watchdog = Watchdog(config, DeviceRegistry(), alerts=alerts)
    with patch("dragonfly.watchdog.monitor.check_service", return_value={"online": False}), \
         patch("dragonfly.watchdog.monitor.check_storage", return_value={"online": True, "healthy": True}), \
         patch("dragonfly.watchdog.monitor.check_camera", return_value={"online": False}), \
         patch("dragonfly.watchdog.monitor.check_recording", return_value={"active": False}):
        watchdog.run_once()
    observations = {call.args[0]: call.args[1] for call in alerts.observe.call_args_list}
    assert observations == {
        "capture": True, "storage": False, "recording:OW": True, "camera:OW": True,
    }


def test_storage_failure_is_reported_without_redundant_recording_failure(tmp_path):
    config = HubConfig(
        storage=StorageConfig(recordings_path=str(tmp_path), mount_point=None),
        cameras=[CameraConfig(id="OW", name="Outdoor West", rtsp_url="rtsp://example/", record=True)],
    )
    config.watchdog.repair_storage = False
    alerts = MagicMock()
    watchdog = Watchdog(config, DeviceRegistry(), alerts=alerts)
    with patch("dragonfly.watchdog.monitor.check_service", return_value={"online": True}), \
         patch("dragonfly.watchdog.monitor.check_storage", return_value={"online": False, "healthy": False}), \
         patch("dragonfly.watchdog.monitor.check_camera", return_value={"online": True}):
        watchdog.run_once()
    observations = {call.args[0]: call.args[1] for call in alerts.observe.call_args_list}
    assert observations == {
        "capture": False, "storage": True, "recording:OW": False, "camera:OW": False,
    }


def test_short_failure_does_not_send():
    sender = MagicMock()
    alerts = AlertManager(sender, delay_s=30)
    with patch("dragonfly.watchdog.alerts.time.monotonic") as clock:
        clock.return_value = 100
        alerts.observe("storage", True, "storage offline")
        clock.return_value = 120
        alerts.observe("storage", False, "storage offline")
        clock.return_value = 200
        alerts._process()
    sender.send.assert_not_called()


def test_slow_notification_does_not_block_health_observations():
    sending = threading.Event()
    release = threading.Event()
    sender = MagicMock()
    sender.send.side_effect = lambda message: (sending.set(), release.wait(5))
    alerts = AlertManager(sender, delay_s=0)
    alerts.observe("camera:OW", True, "camera offline")
    worker = threading.Thread(target=alerts._process)
    worker.start()
    try:
        assert sending.wait(1)
        alerts.observe("camera:OW", False, "camera offline")
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_notification_test_command_sends_test_message():
    sender = MagicMock()
    with patch("sys.argv", ["alerts", "--test"]), \
         patch.object(Ntfy, "from_environment", return_value=sender):
        assert main() == 0
    sender.send.assert_called_once_with("Dragonfly: notification test")
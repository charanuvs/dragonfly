from unittest.mock import MagicMock, patch

from dragonfly.dashboard.live import LiveStreamManager


def test_live_stream_maps_optional_audio_to_aac(tmp_path):
    manager = LiveStreamManager({"OW": "rtsp://example/stream"}, base_dir=tmp_path)
    process = MagicMock()
    process.poll.return_value = None

    try:
        with patch("dragonfly.dashboard.live.subprocess.Popen", return_value=process) as popen:
            manager.ensure_running("OW")

        command = popen.call_args.args[0]
        assert command[command.index("-map") + 1] == "0:v:0"
        audio_map = command.index("-map", command.index("-map") + 1)
        assert command[audio_map + 1] == "0:a?"
        assert command[command.index("-c:a") + 1] == "aac"
        assert command[command.index("-b:a") + 1] == "64k"
    finally:
        manager.stop_all()
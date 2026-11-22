from unittest.mock import MagicMock, patch

from dragonfly.services.dfly_capture.live import LiveStreamManager


def test_live_stream_maps_optional_audio_to_aac(tmp_path):
    manager = LiveStreamManager({"OW": "rtsp://example/stream"}, base_dir=tmp_path)
    process = MagicMock()
    process.poll.return_value = None

    try:
        with patch("dragonfly.services.dfly_capture.live.subprocess.Popen", return_value=process) as popen:
            manager.ensure_running("OW")

        command = popen.call_args.args[0]
        assert command[command.index("-map") + 1] == "0:v:0"
        audio_map = command.index("-map", command.index("-map") + 1)
        assert command[audio_map + 1] == "0:a?"
        assert command[command.index("-c:a") + 1] == "aac"
        assert command[command.index("-b:a") + 1] == "64k"
        assert "-vf" not in command
        assert command[command.index("-c:v") + 1] == "copy"
    finally:
        manager.stop_all()


def test_timestamp_is_opt_in_and_uses_live_local_time(tmp_path):
    manager = LiveStreamManager({"tablet": "rtsp://example/stream"}, base_dir=tmp_path, timestamp_cameras={"tablet"})
    process = MagicMock()
    process.poll.return_value = None
    try:
        with patch("dragonfly.services.dfly_capture.live.subprocess.Popen", return_value=process) as popen:
            manager.ensure_running("tablet")
        command = popen.call_args.args[0]
        assert "%{localtime}" in command[command.index("-vf") + 1]
        assert command[command.index("-c:v") + 1] == "libx264"
    finally:
        manager.stop_all()
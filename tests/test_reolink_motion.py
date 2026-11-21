import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dragonfly.services.dfly_detect.motion import append_motion_marker, monitor_motion
from dragonfly.common.recordings import compute_segment_path


def test_append_motion_marker_uses_the_matching_recording_segment(tmp_path):
    timestamp = 1794355200.5
    segment = compute_segment_path(
        datetime.fromtimestamp(timestamp).astimezone(), tmp_path / "OW", 300
    )
    segment.parent.mkdir(parents=True)
    segment.write_bytes(b"video")

    assert append_motion_marker("OW", tmp_path, 300, timestamp) == segment
    marker = segment.with_suffix(".motion.jsonl")
    assert json.loads(marker.read_text()) == {"ts": timestamp}


def test_append_motion_marker_skips_unmounted_recording_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("dragonfly.services.dfly_detect.motion.os.path.ismount", lambda _path: False)

    assert append_motion_marker("OW", tmp_path, 300, 1794355200.5, "/recordings") is None
    assert not list(tmp_path.rglob("*.motion.jsonl"))


def test_motion_monitor_notifies_only_on_motion_start(monkeypatch, tmp_path):
    timestamp = 1794355200.5
    segment = compute_segment_path(
        datetime.fromtimestamp(timestamp).astimezone(), tmp_path / "OW", 300
    )
    segment.parent.mkdir(parents=True)
    segment.write_bytes(b"video")
    states = iter([False, True, True, False, True])
    people_states = iter([False, False, True, False, True])
    alerts = MagicMock()

    class FakeHost:
        async def get_host_data(self):
            pass

        def supported(self, channel, capability):
            return channel == 0 and capability == "motion_detection"

        def ai_supported(self, channel, object_type):
            return channel == 0 and object_type == "people"

        async def get_motion_state(self, channel):
            try:
                return next(states)
            except StopIteration:
                raise asyncio.CancelledError

        async def get_ai_state(self, channel):
            try:
                return {"people": next(people_states)}
            except StopIteration:
                raise asyncio.CancelledError

        async def logout(self):
            pass

    monkeypatch.setattr("dragonfly.services.dfly_detect.motion.Host", lambda *args, **kwargs: FakeHost())
    monkeypatch.setattr("dragonfly.services.dfly_detect.motion.time.time", lambda: timestamp)

    async def no_wait(_seconds):
        pass

    monkeypatch.setattr("dragonfly.services.dfly_detect.motion.asyncio.sleep", no_wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            monitor_motion(
                "OW",
                "Outdoor West",
                "rtsp://admin:secret@camera.local/main",
                str(tmp_path),
                300,
                None,
                alerts,
            )
        )

    marker = segment.with_suffix(".motion.jsonl")
    assert len(marker.read_text().splitlines()) == 2
    assert alerts.notify.call_count == 2
    assert all("Person detected: Outdoor West" in call.args[0] for call in alerts.notify.call_args_list)

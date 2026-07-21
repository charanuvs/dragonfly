import pytest

from dragonfly.modules.camera.rtsp_camera import parse_rtsp_target


def test_parse_rtsp_target_default_port():
    host, port = parse_rtsp_target("rtsp://user:pass@192.168.69.50/stream1")
    assert host == "192.168.69.50"
    assert port == 554


def test_parse_rtsp_target_explicit_port():
    host, port = parse_rtsp_target("rtsp://192.168.69.50:8554/stream1")
    assert host == "192.168.69.50"
    assert port == 8554


def test_parse_rtsp_target_no_host_raises():
    with pytest.raises(ValueError):
        parse_rtsp_target("not-a-url")

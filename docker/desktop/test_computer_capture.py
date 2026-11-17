from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from computer_capture import Screen, mask_rgb


def test_password_mask_clips_pads_and_preserves_other_rgb_pixels():
    pixels = bytes([71, 83, 97]) * 20 * 12
    result, boxes = mask_rgb(pixels, 20, 12, 60, 3, [(-2, 2, 4, 3), (100, 100, 3, 3)])
    assert boxes == [(0, 0, 5, 8)]
    for y in range(12):
        for x in range(20):
            assert result[(y * 20 + x) * 3:(y * 20 + x + 1) * 3] == (
                b'\0\0\0' if x < 5 and y < 8 else bytes([71, 83, 97]))


def test_capture_alpha_and_stride_padding_do_not_leak_into_rgb_output():
    row = bytes([11, 22, 33, 255, 44, 55, 66, 255, 99, 99, 99, 99])
    result, boxes = mask_rgb(row * 2, 2, 2, 12, 4, [])
    assert result == bytes([11, 22, 33, 44, 55, 66]) * 2
    assert not boxes
    with pytest.raises(ValueError, match='Truncated'):
        mask_rgb(b'', 2, 2, 6, 3, [])


def test_capture_never_queries_application_accessibility():
    frame = SimpleNamespace(get_width=lambda: 30, get_height=lambda: 20,
        get_rowstride=lambda: 90, get_n_channels=lambda: 3,
        get_pixels=lambda: bytes([255]) * 30 * 20 * 3)
    screen = Screen.__new__(Screen)
    screen.root = frame
    screen.Gdk = SimpleNamespace(pixbuf_get_from_window=lambda *_: frame)
    def blocked_app():
        raise AssertionError('Capture must never call application accessibility')
    screen.regions = blocked_app
    pixels, width, height, metadata = screen.capture()
    assert (width, height) == (30, 20)
    assert pixels == bytes([255]) * 30 * 20 * 3
    assert metadata['policy'] == 'desktop-pixels-v1'
    assert metadata['regions'] == []
    assert 'no automatic redaction' in metadata['scope']

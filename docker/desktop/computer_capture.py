"""Capture desktop pixels without querying applications or accessibility trees.

A recursive AT-SPI Collection query can hang Calc's UI thread while enumerating
its virtual spreadsheet cells. Recording must not drive or inspect applications.
The captured pixels match VNC; receipts explicitly describe unredacted capture.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

POLICY = "desktop-pixels-v1"
SCOPE = "Desktop pixels as displayed; no automatic redaction"


def mask_rgb(pixels, width, height, stride, channels, regions):
    """Return tightly packed RGB; clip padded rectangles in screen coordinates."""
    if channels not in (3, 4) or stride < width * channels:
        raise ValueError("Unsupported capture pixel layout")
    result = bytearray(width * height * 3)
    for y in range(height):
        row = pixels[y * stride:y * stride + width * channels]
        if len(row) != width * channels:
            raise ValueError("Truncated capture")
        if channels == 4:
            row = bytes(value for index, value in enumerate(row) if index % 4 != 3)
        result[y * width * 3:(y + 1) * width * 3] = row
    boxes = set()
    for x, y, w, h in regions:
        if w <= 0 or h <= 0:
            continue
        left, top = max(0, x - 3), max(0, y - 3)
        right, bottom = min(width, x + w + 3), min(height, y + h + 3)
        if right <= left or bottom <= top:
            continue
        boxes.add((left, top, right - left, bottom - top))
        blank = bytes((right - left) * 3)
        for yy in range(top, bottom):
            offset = (yy * width + left) * 3
            result[offset:offset + len(blank)] = blank
    return bytes(result), sorted(boxes)


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


class Screen:
    def __init__(self):
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GdkPixbuf, GLib
        self.Gdk, self.Pixbuf, self.GLib = Gdk, GdkPixbuf, GLib
        self.root = Gdk.get_default_root_window()
        if self.root is None:
            raise RuntimeError("No X11 display")

    def capture(self):
        frame = self.Gdk.pixbuf_get_from_window(self.root, 0, 0,
            self.root.get_width(), self.root.get_height())
        if frame is None:
            raise RuntimeError("X11 capture failed")
        width, height = frame.get_width(), frame.get_height()
        pixels, _ = mask_rgb(frame.get_pixels(), width, height, frame.get_rowstride(),
                            frame.get_n_channels(), [])
        # Keep the receipt shape readable alongside older masked recordings.
        return pixels, width, height, {"policy": POLICY, "regions": [],
            "incomplete": False, "detector_unavailable": False,
            "captured_at": time.time(), "scope": SCOPE}

    def pixbuf(self, pixels, width, height):
        return self.Pixbuf.Pixbuf.new_from_bytes(self.GLib.Bytes.new(pixels),
            self.Pixbuf.Colorspace.RGB, False, 8, width, height, width * 3)

    def png(self, path):
        pixels, width, height, metadata = self.capture()
        # Save atomically so viewers never read a partial image.
        temporary = path.with_suffix(".tmp")
        self.pixbuf(pixels, width, height).savev(str(temporary), "png", [], [])
        temporary.replace(path)
        return {"width": width, "height": height, "masking": metadata}


def record(screen, args):
    width, height = map(int, args.resolution.split("x"))
    directory = Path(args.record)
    index = directory / f"segments-{args.start_number:06d}.csv"
    receipt = directory / f"masking-{args.start_number:06d}.json"
    statistics = {"policy": POLICY, "scope": SCOPE, "frames": 0,
                  "masked_frames": 0, "incomplete_frames": 0, "detector_unavailable_frames": 0,
                  "recording": True}
    process = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-use_wallclock_as_timestamps", "1", "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", args.resolution, "-framerate", "2", "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-r", "2", "-fps_mode", "cfr", "-g", "60",
        "-force_key_frames", "expr:gte(t,n_forced*30)", "-f", "segment",
        "-segment_time", "30", "-reset_timestamps", "1",
        "-segment_start_number", str(args.start_number),
        "-segment_format_options", "movflags=+faststart", "-segment_list", str(index),
        "-segment_list_type", "csv", str(directory / "%06d.mp4")],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        next_frame = time.monotonic()
        while not stopped:
            pixels, captured_width, captured_height, metadata = screen.capture()
            if (captured_width, captured_height) != (width, height):
                # Preserve fixed segment dimensions if the desktop resolution changes.
                frame = screen.pixbuf(pixels, captured_width, captured_height).scale_simple(
                    width, height, screen.Pixbuf.InterpType.BILINEAR)
                pixels, _ = mask_rgb(frame.get_pixels(), width, height,
                    frame.get_rowstride(), frame.get_n_channels(), [])
            process.stdin.write(pixels)
            process.stdin.flush()
            statistics["frames"] += 1
            statistics["masked_frames"] += bool(metadata["regions"])
            statistics["incomplete_frames"] += metadata["incomplete"]
            statistics["detector_unavailable_frames"] += metadata["detector_unavailable"]
            statistics["last_frame_at"] = metadata["captured_at"]
            atomic_json(receipt, statistics)
            next_frame = max(next_frame + .5, time.monotonic())
            time.sleep(max(0, next_frame - time.monotonic()))
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            result = process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("Video encoder did not finalize")
        statistics["recording"] = False
        atomic_json(receipt, statistics)
        if result:
            raise RuntimeError("Video encoder failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--png", type=Path)
    mode.add_argument("--record", type=Path)
    parser.add_argument("--resolution", default="1280x720")
    parser.add_argument("--start-number", type=int, default=0)
    args = parser.parse_args()
    os.environ["DISPLAY"] = args.display
    screen = Screen()
    if args.png:
        print(json.dumps(screen.png(args.png)))
    else:
        record(screen, args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Read-only display check: desktop PNG, real two-FPS segments and finalization.

Run as the desktop user. Creates a temporary evidence directory; does not drive
the desktop or change its current run. Evidence contains the displayed pixels.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-dir', default='/opt/calliope-desktop')
parser.add_argument('--display', default=':1')
parser.add_argument('--seconds', type=float, default=35)
args = parser.parse_args()
sys.path.insert(0, args.source_dir)
import os
os.environ['DISPLAY'] = args.display
from computer_capture import Screen
from computer_runtime import Recorder

screen = Screen()
directory = Path(tempfile.mkdtemp(prefix='calliope-capture-check-'))
metadata = screen.png(directory / 'desktop.png')
assert metadata['masking']['policy'] == 'desktop-pixels-v1'

def decode(path, *, seek=None):
    command = ['ffmpeg', '-v', 'error']
    if seek is not None:
        command += ['-ss', str(seek)]
    command += ['-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    return subprocess.check_output(command)

width, height = metadata['width'], metadata['height']
pixels = decode(directory / 'desktop.png')
assert len(pixels) == width * height * 3
assert any(pixels), 'Entire screenshot is blank'
recorder = Recorder(directory / 'video', args.display, f'{width}x{height}')
started = time.monotonic()
try:
    while time.monotonic() - started < args.seconds:
        assert recorder.alive(), (directory / 'video/capture.log').read_text()
        time.sleep(.5)
finally:
    manifest = recorder.stop()
assert manifest['segments']
assert not manifest['recording']
assert manifest['masking_captures'][-1]['frames'] > 0
assert not manifest['masking_captures'][-1]['detector_unavailable_frames']
probes = []
for segment in manifest['segments']:
    path = directory / 'video' / segment['file']
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error',
        '-show_entries', 'stream=width,height,avg_frame_rate:format=duration', '-of', 'json', str(path)]))
    assert probe['streams'][0]['avg_frame_rate'] == '2/1', probe
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'], check=True)
    probes.append(probe)
elapsed = sum(float(p['format']['duration']) for p in probes)
assert abs(elapsed - args.seconds) < 2, {'video_seconds': elapsed, 'wall_seconds': args.seconds}
after = screen.png(directory / 'desktop-after.png')
print(json.dumps({'directory': str(directory), 'screenshot': metadata, 'manifest': manifest,
                  'video_seconds': elapsed}, indent=2))

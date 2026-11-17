#!/usr/bin/env python3
"""Use the same renderer inspection/actions as desktop, through private ADB run-as."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

class Device:
    def __init__(self, serial, adb=None):
        self.adb = [adb or shutil.which('adb') or str(Path.home()/'.local/share/calliope-mobile/platform-tools/adb'), '-s', serial]

    def probe(self, request):
        result = subprocess.run(self.adb+['shell', '-T', 'run-as', 'ai.rvbbit.calliope.mobile', 'files/ui-probe'], input=(json.dumps(request)+'\n').encode(), capture_output=True, check=True, timeout=12)
        return json.loads(result.stdout)

    def capture(self, path):
        result = subprocess.run(self.adb+['exec-out', 'screencap', '-p'], capture_output=True, check=True, timeout=10)
        Path(path).write_bytes(result.stdout)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    args = parser.parse_args()
    print(json.dumps(Device(args.serial).probe(json.load(sys.stdin)), indent=2))

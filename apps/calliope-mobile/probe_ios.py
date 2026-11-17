#!/usr/bin/env python3
"""Private structural inspection on the Mac's named iOS Simulator (no mouse)."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


class Simulator:
    def __init__(self, device):
        self.device = device

    def probe(self, request):
        container = subprocess.check_output([
            'xcrun', 'simctl', 'get_app_container', self.device,
            'ai.rvbbit.calliope.mobile', 'data',
        ], text=True).strip()
        directory = Path(container) / 'Library/Application Support/Calliope'
        # macOS Unix socket paths have a 104-byte limit. Resolve relative to the
        # private app directory, just as the simulator host does when binding.
        previous = Path.cwd()
        try:
            os.chdir(directory)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(10)
                client.connect('ui-probe.sock')
                client.sendall((json.dumps(request) + '\n').encode())
                return json.loads(client.makefile('rb').readline(8_388_608))
        finally:
            os.chdir(previous)

    def capture(self, path):
        subprocess.run(['xcrun', 'simctl', 'io', self.device, 'screenshot', str(path)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='Explicit simctl device UUID')
    parser.add_argument('--screenshot')
    args = parser.parse_args()
    simulator = Simulator(args.device)
    if args.screenshot:
        simulator.capture(args.screenshot)
    else:
        print(json.dumps(simulator.probe(json.load(sys.stdin)), indent=2))

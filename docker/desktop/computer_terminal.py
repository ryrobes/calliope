"""Run one shell job in a PTY and retain an asciicast v2 output-only replay."""
import codecs
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import struct
import subprocess
import sys
import termios
import time


def main():
    script = Path(sys.argv[1])
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
    mode = termios.tcgetattr(slave)
    mode[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, mode)
    env = {**os.environ, 'TERM': 'xterm-256color', 'COLUMNS': '100', 'LINES': '24'}
    started = time.monotonic()
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    with script.with_name('terminal.cast').open('a', buffering=1) as tape:
        # stdin is closed: this is a submitted command, not an unattended
        # password prompt. Output still has a real terminal for progress/TUI.
        process = subprocess.Popen(['bash', str(script)], stdin=subprocess.DEVNULL,
                                   stdout=slave, stderr=slave, env=env)
        os.close(slave)
        try:
            while True:
                try:
                    raw = os.read(master, 8192)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not raw:
                    break
                text = decoder.decode(raw)
                if text:
                    tape.write(json.dumps([round(time.monotonic() - started, 6), 'o', text], ensure_ascii=False) + '\n')
                    sys.stdout.write(text)
                    sys.stdout.flush()
            tail = decoder.decode(b'', final=True)
            if tail:
                tape.write(json.dumps([round(time.monotonic() - started, 6), 'o', tail]) + '\n')
                sys.stdout.write(tail)
            tape.flush()
            os.fsync(tape.fileno())
        finally:
            os.close(master)
        return process.wait()


if __name__ == '__main__':
    raise SystemExit(main())

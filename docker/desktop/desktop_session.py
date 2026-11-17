"""Use the actual desktop session bus instead of autospawning a job-owned bus."""
import os
from pathlib import Path


def session_environment(proc=Path('/proc')):
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid() or (process / 'comm').read_text().strip() != 'xfce4-session':
                continue
            values = {}
            for item in (process / 'environ').read_bytes().split(b'\0'):
                key, _, value = item.partition(b'=')
                if key in (b'DBUS_SESSION_BUS_ADDRESS', b'XDG_RUNTIME_DIR'):
                    values[key.decode()] = value.decode()
            if values.get('DBUS_SESSION_BUS_ADDRESS'):
                return values
        except (OSError, UnicodeError):
            continue
    return {}

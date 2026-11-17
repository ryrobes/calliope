from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from desktop_session import session_environment


def test_only_desktop_session_bus_is_inherited(tmp_path):
    p = tmp_path / '123'; p.mkdir()
    (p / 'comm').write_text('xfce4-session\n')
    (p / 'environ').write_bytes(b'DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/desktop\0XDG_RUNTIME_DIR=/run/user/1000\0SECRET=hidden\0DISPLAY=:99\0')
    assert session_environment(tmp_path) == {'DBUS_SESSION_BUS_ADDRESS':'unix:path=/tmp/desktop', 'XDG_RUNTIME_DIR':'/run/user/1000'}
    (p / 'comm').write_text('bash\n')
    assert session_environment(tmp_path) == {}

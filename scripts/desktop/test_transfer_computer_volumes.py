import importlib.util
import json
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('transfer', Path(__file__).with_name('transfer-computer-volumes.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


@pytest.fixture
def host(tmp_path, monkeypatch):
    mounts = []
    volumes = {}
    for key, destination in transfer.VOLUMES.items():
        source = tmp_path / key
        source.mkdir()
        (source / 'document').write_text('Preserved café\n')
        (source / 'document').chmod(0o640)
        (source / 'shortcut').symlink_to('document')
        mounts.append({'Destination': destination, 'Type': 'volume', 'Source': str(source), 'Name': key})
    item = {'State': {'Running': False}, 'Mounts': mounts, 'Image': 'test-id',
            'Config': {'Image': 'test-image', 'Env': ['SECRET=must-not-copy']},
            'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}}}

    def docker(*args):
        if args[0] == 'inspect':
            return json.dumps([item])
        if args[:2] == ('volume', 'ls'):
            return '\n'.join(volumes)
        if args[:2] == ('volume', 'create'):
            path = tmp_path / args[-1]
            path.mkdir()
            volumes[args[-1]] = path
            return args[-1]
        if args[:2] == ('volume', 'inspect'):
            return json.dumps([{'Mountpoint': str(volumes[args[-1]])}])
        raise AssertionError(args)

    monkeypatch.setattr(transfer, 'docker', docker)
    return item, volumes


def test_roundtrip_preserves_data_links_modes_and_refuses_overwrite(tmp_path, host):
    directory = tmp_path / 'backup'
    transfer.backup('desktop', directory)
    assert 'must-not-copy' not in (directory / 'manifest.json').read_text()
    transfer.restore(directory, 'new')
    for path in host[1].values():
        assert (path / 'document').read_text() == 'Preserved café\n'
        assert (path / 'document').stat().st_mode & 0o777 == 0o640
        assert (path / 'document').stat().st_uid == os.getuid()
        assert (path / 'shortcut').readlink() == Path('document')
    with pytest.raises(ValueError, match='must not exist'):
        transfer.restore(directory, 'new')


def test_running_source_is_rejected_before_backup_creation(tmp_path, host):
    host[0]['State']['Running'] = True
    with pytest.raises(ValueError, match='Stop the source'):
        transfer.backup('desktop', tmp_path / 'backup')
    assert not (tmp_path / 'backup').exists()


def test_corruption_of_second_archive_creates_no_volumes(tmp_path, host):
    directory = tmp_path / 'backup'
    transfer.backup('desktop', directory)
    with (directory / 'home.tar').open('ab') as output:
        output.write(b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        transfer.restore(directory, 'new')
    assert host[1] == {}

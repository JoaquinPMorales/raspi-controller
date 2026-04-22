import os
import sys
import shutil
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from copier import ExternalCopier, _resolve_rsync_bin


def make_copier(tmp_path, options=None, *, use_key=False, password=None):
    key_path = None
    if use_key:
        key_path = tmp_path / "id_rsa"
        key_path.write_text("dummy-key")

    return ExternalCopier(
        {
            'host': '192.168.1.50',
            'user': 'pi',
            'port': 2222,
            'key_path': str(key_path) if key_path else None,
            'password': password,
        },
        {'local_destination': '/tmp/downloads'},
        options or {},
    )


def test_build_rsync_args_for_single_file_preserves_filename_destination(tmp_path, monkeypatch):
    copier = make_copier(tmp_path, use_key=True)
    monkeypatch.delenv('RSYNC_BIN', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/rsync' if name == 'rsync' else None)

    args, env = copier._build_rsync_args(
        '/mnt/storage/downloads/Movie Name (2024).mkv',
        '/tmp/downloads/Movies/Movie Name (2024)',
        source_is_dir=False,
    )

    assert args[-2] == 'pi@192.168.1.50:/mnt/storage/downloads/Movie Name (2024).mkv'
    assert args[-1] == '/tmp/downloads/Movies/Movie Name (2024)/'
    assert '--protect-args' in args
    assert env.get('SSHPASS') is None


def test_build_rsync_args_for_directory_copies_contents(tmp_path, monkeypatch):
    copier = make_copier(tmp_path, use_key=True)
    monkeypatch.delenv('RSYNC_BIN', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/rsync' if name == 'rsync' else None)

    args, _ = copier._build_rsync_args(
        '/mnt/storage/downloads/Show Name S01',
        '/tmp/downloads/TV/Show Name/Season 01',
        source_is_dir=True,
    )

    assert args[-2] == 'pi@192.168.1.50:/mnt/storage/downloads/Show Name S01/'
    assert args[-1] == '/tmp/downloads/TV/Show Name/Season 01/'


def test_build_rsync_transport_uses_sshpass_for_password_auth(tmp_path, monkeypatch):
    copier = make_copier(tmp_path, password='secret')
    monkeypatch.setattr(shutil, 'which', lambda name: f'/usr/bin/{name}')

    transport, env = copier._build_rsync_transport()

    assert transport.startswith('/usr/bin/sshpass -e ssh ')
    assert env['SSHPASS'] == 'secret'


def test_resolve_rsync_bin_uses_path_lookup(monkeypatch):
    monkeypatch.delenv('RSYNC_BIN', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda name: '/opt/homebrew/bin/rsync' if name == 'rsync' else None)

    assert _resolve_rsync_bin() == '/opt/homebrew/bin/rsync'


def test_resolve_rsync_bin_raises_clear_error_when_missing(monkeypatch):
    monkeypatch.delenv('RSYNC_BIN', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    monkeypatch.setattr(os.path, 'exists', lambda path: False)

    with pytest.raises(FileNotFoundError, match='machine running this script'):
        _resolve_rsync_bin()


def test_copy_single_item_uses_scanned_type_metadata(tmp_path, monkeypatch):
    copier = make_copier(tmp_path, use_key=True)
    captured = {}

    def fake_copy_with_rsync(self, source_path, dest_path, console, progress, task_id, display_name, progress_callback=None, source_is_dir=True):
        captured['source_path'] = source_path
        captured['dest_path'] = dest_path
        captured['display_name'] = display_name
        captured['source_is_dir'] = source_is_dir
        return True

    monkeypatch.setattr(ExternalCopier, '_copy_with_rsync', fake_copy_with_rsync)

    item = {
        'show': 'Movie Name',
        'season': None,
        'path': '/mnt/storage/downloads/Movie Name (2024).mkv',
        'content_type': 'movie',
        'items': [{'type': 'file'}],
    }

    assert copier._copy_single_item(item, console=None, progress=None, task_id=1) is True
    assert captured == {
        'source_path': '/mnt/storage/downloads/Movie Name (2024).mkv',
        'dest_path': '/tmp/downloads/Movies/Movie Name',
        'display_name': 'Movie Name',
        'source_is_dir': False,
    }

import os
import sys

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from copier import RsyncCopier


def make_copier(paths_config=None):
    return RsyncCopier(
        {'host': '192.168.1.50', 'user': 'pi'},
        paths_config or {},
        {},
    )


def test_get_destination_path_raises_for_missing_tv_destination():
    copier = make_copier()

    with pytest.raises(ValueError, match='jellyfin_shows, jellyfin_tv'):
        copier._get_destination_path({'show': 'Show Name', 'content_type': 'tv', 'season': '01'})


def test_get_destination_path_raises_for_missing_movie_destination():
    copier = make_copier()

    with pytest.raises(ValueError, match='jellyfin_movies'):
        copier._get_destination_path({'show': 'Movie Name', 'content_type': 'movie'})


def test_remote_existing_series_folder_is_reused():
    copier = make_copier({'jellyfin_shows': '/mnt/media/Shows'})

    class Channel:
        def recv_exit_status(self):
            return 0

    class Stdout:
        channel = Channel()

        def read(self):
            return b'Show Name (2024)\nOther Show\n'

    class SSH:
        def exec_command(self, command):
            return None, Stdout(), None

    existing = copier._find_existing_series_folder_remote(SSH(), '/mnt/media/Shows', 'Show Name')

    assert existing == 'Show Name (2024)'
    assert copier._get_destination_path(
        {'show': 'Show Name', 'content_type': 'tv', 'season': '02'}, existing
    ) == '/mnt/media/Shows/Show Name (2024)/Season 02'


def test_copy_items_expands_separately_downloaded_sources(monkeypatch):
    copier = make_copier({'jellyfin_shows': '/mnt/media/Shows'})
    copied_paths = []

    def fake_copy(self, item, console, progress, task_id, progress_callback=None, item_num=1, total_items=1):
        copied_paths.append(item['path'])
        return True

    monkeypatch.setattr(RsyncCopier, '_copy_single_item', fake_copy)
    item = {
        'show': 'Show Name',
        'season': '01',
        'content_type': 'tv',
        'path': '/downloads/part-1',
        'items': [
            {'path': '/downloads/part-1', 'type': 'folder'},
            {'path': '/downloads/part-2', 'type': 'folder'},
        ],
    }

    assert copier.copy_items([item], Console()) is True
    assert copied_paths == ['/downloads/part-1', '/downloads/part-2']

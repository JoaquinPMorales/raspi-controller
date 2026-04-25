import os
import sys

import pytest

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

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import download_global_tiles as tiles


JPEG = b'\xff\xd8\xfftest\xff\xd9'


def test_six_workers_cover_level_once_and_resume_without_network(tmp_path):
    with patch.object(tiles, 'fetch', return_value=JPEG) as fetch:
        for worker in range(6):
            tiles.worker(str(tmp_path), 'satellite', 2, worker)
        assert fetch.call_count == 16
    assert len(list((tmp_path / 'satellite').rglob('*.jpg'))) == 16
    states = [json.loads(p.read_text()) for p in (tmp_path / 'output').glob('*.json')]
    assert sum(s['tiles'] for s in states) == 16
    assert all(s['done'] for s in states)
    with patch.object(tiles, 'fetch', side_effect=AssertionError('Unexpected redownload')):
        for worker in range(6):
            tiles.worker(str(tmp_path), 'satellite', 2, worker)


def test_invalid_response_never_publishes_or_advances_checkpoint(tmp_path):
    with patch.object(tiles, 'fetch', return_value=b'<html>error</html>'), patch.object(tiles.time, 'sleep'):
        with pytest.raises(ValueError, match='Invalid image'):
            tiles.worker(str(tmp_path), 'roadmap', 0, 0)
    state = json.loads((tmp_path / 'output' / 'roadmap-0-0.json').read_text())
    assert state['next'] == 0 and state['tiles'] == 0 and not state['done']
    assert state['error']
    assert not list((tmp_path / 'roadmap').rglob('*.png'))


def test_file_published_before_interruption_is_reused(tmp_path):
    target = tmp_path / 'satellite' / '0' / '0' / '0.jpg'
    target.parent.mkdir(parents=True)
    target.write_bytes(JPEG)
    with patch.object(tiles, 'fetch', side_effect=AssertionError('Unexpected redownload')):
        state = tiles.worker(str(tmp_path), 'satellite', 0, 0)
    assert state['tiles'] == 1 and state['done']

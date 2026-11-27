"""Remove only the electronic map archive after stopping the downloader."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.download_global_tiles import save, snapshot, stamp


def main():
    root = Path('/srv/terrain/imagery-global')
    output = root / 'output'
    target = root / 'roadmap'
    assert root.resolve() == root and target.resolve() == target
    assert target.is_dir() and not target.is_symlink() and not os.path.ismount(target)
    assert not any(p.is_symlink() for p in target.rglob('*'))
    if not (root / 'STOP').is_file():
        raise RuntimeError('STOP is required before deletion')
    with (output / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = json.loads((output / 'plan.json').read_bytes())
        assert {s: v['layer'] for s, v in plan['sources'].items()} == {
            'satellite': 's', 'roadmap': 'm', 'overlay': 'h'}
        preserved = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                     for source in ('satellite', 'overlay') for p in output.glob(source + '-*.json')}
        checkpoints = list(output.glob('roadmap-*'))
        assert all(p.is_file() and not p.is_symlink() and
                   re.fullmatch(r'roadmap-\d+-[0-5]\.(json|tmp)', p.name) for p in checkpoints)
        tile_files = [p for p in target.rglob('*') if p.is_file()]
        tile_count, tile_bytes = len(tile_files), sum(p.stat().st_size for p in tile_files)
        del plan['sources']['roadmap']
        save(output / 'plan.json', plan)
        shutil.rmtree(target)
        for path in checkpoints:
            path.unlink()
        assert not target.exists() and not list(output.glob('roadmap-*'))
        assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == sha
                   for name, sha in preserved.items())
        old_status = json.loads((output / 'status.json').read_bytes())
        save(output / 'status.json', snapshot(root, plan, 'stopped', old_status['level']))
        receipt = {'updated_at': stamp(), 'removed_source': {'roadmap': 'm'},
                   'deleted_tile_files': tile_count, 'deleted_tile_bytes': tile_bytes,
                   'deleted_checkpoint_files': len(checkpoints), 'preserved_checkpoints': preserved}
        save(output / 'electronic_map_removal.json', receipt)
        (root / 'STOP').unlink()
        print(json.dumps({k: v for k, v in receipt.items() if k != 'preserved_checkpoints'}))


if __name__ == '__main__':
    main()

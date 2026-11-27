"""Extend the active two-source plan without changing any existing tile or checkpoint."""
from pathlib import Path
import fcntl
import hashlib
import json
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.download_global_tiles import ROAD_STYLE, save, stamp


def main():
    root = Path('/srv/terrain/imagery-global')
    output = root / 'output'
    if not (root / 'STOP').exists():
        raise RuntimeError('Create STOP and wait for workers to exit before extending the plan')
    with (output / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in output.glob('*-*-*.json')}
        path = output / 'plan.json'
        raw = path.read_bytes()
        plan = json.loads(raw)
        binding = {source: item['layer'] for source, item in plan['sources'].items()}
        if binding != {'satellite': 's', 'roadmap': 'm'}:
            raise ValueError('Expected the verified original Google s/m plan')
        plan['sources']['overlay'] = {
            'service': 'Google Maps road labels', 'layer': 'h', 'max_level': 22,
            'credit': 'Google Maps',
            'apistyle': ROAD_STYLE,
            'url': 'https://mt1.google.com/vt/?lyrs=h&x={x}&y={y}&z={z}&apistyle=' + quote(ROAD_STYLE, safe='')}
        save(path, plan)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in output.glob('*-*-*.json')}
        if before != after:
            raise RuntimeError('Existing checkpoint hashes changed')
        receipt = {'updated_at': stamp(), 'added': {'overlay': 'h'},
                   'old_plan_sha256': hashlib.sha256(raw).hexdigest(),
                   'new_plan_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'preserved_checkpoints': before, 'redownload_existing': False}
        save(output / 'overlay_extension.json', receipt)
        (root / 'STOP').unlink()
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()

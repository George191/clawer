"""Download XYZ satellite/street tiles on the terrain host, coarse levels first."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
import urllib.request
from urllib.parse import quote

SOURCES = {'satellite': 's', 'overlay': 'h'}
BASE = 'https://mt1.google.com/vt/'
MAX_LEVEL = 22
ROAD_STYLE = 's.t:0|s.e:l|p.v:off'
WORKERS = 6
BATCH = 3000


def stamp():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temporary, path)


def fetch(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'EtheriaOfflineTiles/1.0 (+https://github.com/George191/situtaion)'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}: {url}')
        return data


def valid_image(data):
    return ((data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'))
            or (data.startswith(b'\x89PNG\r\n\x1a\n') and data.endswith(b'IEND\xaeB`\x82')))


def worker(root_string, source, level, number):
    if source not in SOURCES:
        raise ValueError('Unsupported tile source: ' + source)
    root = Path(root_string)
    checkpoint = root / 'output' / f'{source}-{level}-{number}.json'
    total = 4 ** level
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        'source': source, 'level': level, 'worker': number, 'next': number,
        'tiles': 0, 'bytes': 0, 'done': False, 'error': None}
    if (state['source'], state['level'], state['worker']) != (source, level, number):
        raise ValueError('Checkpoint binding differs')
    if state['done']:
        return state
    extension = 'jpg' if source == 'satellite' else 'png'
    state['error'] = None
    try:
        while state['next'] < total:
            end = min(total, state['next'] + WORKERS * BATCH)
            for index in range(state['next'], end, WORKERS):
                if (root / 'STOP').exists():
                    raise RuntimeError('STOP requested')
                if shutil.disk_usage(root).free < 10 * 1024 ** 3:
                    raise RuntimeError('Less than 10 GiB disk space remains')
                x, y = divmod(index, 2 ** level)
                target = root / source / str(level) / str(x) / f'{y}.{extension}'
                if target.exists():
                    data = target.read_bytes()
                    if not valid_image(data) or source == 'overlay' and not data.startswith(b'\x89PNG\r\n\x1a\n'):
                        raise ValueError(f'Invalid existing tile: {target}')
                else:
                    url = BASE + f'?lyrs={SOURCES[source]}&x={x}&y={y}&z={level}'
                    if source == 'overlay':
                        url += '&apistyle=' + quote(ROAD_STYLE, safe='')
                    for attempt in range(8):
                        try:
                            data = fetch(url)
                            if not valid_image(data) or source == 'overlay' and not data.startswith(b'\x89PNG\r\n\x1a\n'):
                                raise ValueError(f'Invalid image response: {url}')
                            break
                        except Exception:
                            if attempt == 7:
                                raise
                            # Allow a brief proxy/TLS outage to recover before failing this tile.
                            time.sleep(min(60, 5 * 2 ** attempt))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    partial = target.with_suffix('.part')
                    with partial.open('wb') as output:
                        output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(partial, target)
                state.update(next=index + WORKERS, tiles=state['tiles'] + 1,
                             bytes=state['bytes'] + len(data), updated_at=stamp())
                # Each worker owns a disjoint stride; a committed checkpoint follows file publication.
                if state['tiles'] % 25 == 0:
                    save(checkpoint, state)
            save(checkpoint, state)
        state.update(done=True, updated_at=stamp())
        save(checkpoint, state)
    except Exception as error:
        state.update(error=f'{type(error).__name__}: {error}', updated_at=stamp())
        save(checkpoint, state)
        raise
    return state


def snapshot(root, plan, phase, level, error=None):
    states = [json.loads(p.read_text()) for p in (root / 'output').glob('*-*-*.json')]
    return {'updated_at': stamp(), 'phase': phase, 'level': level, 'error': error,
            'root': str(root), 'workers': WORKERS, 'batch_limit': BATCH,
            'network': 'workstation SSH proxy' if os.environ.get('HTTPS_PROXY') else 'direct',
            'order': 'ascending_xyz', 'sources': plan['sources'],
            'tiles': sum(s['tiles'] for s in states), 'bytes': sum(s['bytes'] for s in states),
            'levels': {source: {str(z): {
                'tiles': sum(s['tiles'] for s in states if s['source'] == source and s['level'] == z),
                'total': 4 ** z,
                'done': sum(s['done'] for s in states if s['source'] == source and s['level'] == z) == WORKERS,
                'errors': [s['error'] for s in states if s['source'] == source and s['level'] == z and s['error']]}
                for z in range(plan['sources'][source]['max_level'] + 1)} for source in SOURCES}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/srv/terrain/imagery-global'))
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    import fcntl
    (root / 'output').mkdir(exist_ok=True)
    with (root / 'output' / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan_file = root / 'output' / 'plan.json'
        if plan_file.exists():
            plan = json.loads(plan_file.read_text())
        else:
            sources = {source: {'service': 'Google Maps ' + {'satellite': 'satellite', 'overlay': 'road labels'}[source],
                                'layer': layer, 'max_level': MAX_LEVEL, 'credit': 'Google Maps',
                                'url': BASE + f'?lyrs={layer}&x={{x}}&y={{y}}&z={{z}}' +
                                       ('&apistyle=' + quote(ROAD_STYLE, safe='') if source == 'overlay' else ''),
                                **({'apistyle': ROAD_STYLE} if source == 'overlay' else {})}
                       for source, layer in SOURCES.items()}
            plan = {'sources': sources, 'created_at': stamp(), 'workers': WORKERS, 'batch_limit': BATCH}
            save(plan_file, plan)
        if {source: item.get('layer') for source, item in plan['sources'].items()} != SOURCES:
            raise ValueError('Existing plan uses a different imagery source; refusing to mix tile archives')
        if plan['sources']['overlay'].get('apistyle') != ROAD_STYLE:
            raise ValueError('Road overlay must use the label-free style; refusing to mix styled tiles')
        level = 0
        try:
            with ProcessPoolExecutor(max_workers=WORKERS) as pool:
                for level in range(max(s['max_level'] for s in plan['sources'].values()) + 1):
                    for source in SOURCES:
                        if level > plan['sources'][source]['max_level']:
                            continue
                        futures = [pool.submit(worker, str(root), source, level, n) for n in range(WORKERS)]
                        while not all(f.done() for f in futures):
                            save(root / 'output' / 'status.json', snapshot(root, plan, 'running', level))
                            time.sleep(2)
                        for future in futures:
                            future.result()
                        save(root / 'output' / 'status.json', snapshot(root, plan, 'running', level))
                    # Level barrier: neither source advances until both complete this level.
            save(root / 'output' / 'status.json', snapshot(root, plan, 'complete', level))
        except Exception as error:
            save(root / 'output' / 'status.json', snapshot(root, plan, 'failed', level, str(error)))
            raise


if __name__ == '__main__':
    main()

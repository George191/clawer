"""Read the terrain host's published tile checkpoints without changing the job."""
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from datetime import datetime, timezone


def credentials():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'output' / 'twitter_schedule_20261027'))
    from environment_rows import FILE, rows
    with ZipFile(FILE) as package:
        rels = {n.get('Id'): n.get('Target') for n in ET.fromstring(package.read('xl/_rels/workbook.xml.rels'))}
        sheet = next(n for n in ET.fromstring(package.read('xl/workbook.xml')).iter()
                     if n.tag.endswith('}sheet') and n.get('name') == 'minio(192.168.20.61)')
        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        target = rels[rid]
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
    cells = {cell: value for name, _, values in rows() if name == target for cell, value in values.items()}
    if cells['B3'].lower() != 'username' or cells['B4'].lower() != 'password' or cells['C3'] != 'minio':
        raise ValueError('Terrain host credential fields changed')
    return cells


async def collect():
    try:
        import asyncssh
        cells = credentials()
        async with asyncssh.connect('192.168.20.61', username=cells['C3'], password=cells['C4'],
                                    client_keys=[], agent_path=None,
                                    known_hosts=str(Path.home() / '.ssh' / 'known_hosts'),
                                    connect_timeout=10, login_timeout=10) as client:
            result = await client.run('cat /srv/terrain/imagery-global/output/status.json', check=True, timeout=10)
        value = json.loads(result.stdout)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['updated_at'])).total_seconds()
        if value['phase'] == 'running' and age > 120:
            value['phase'] = 'stale — 下载进程未更新，需检查'
        value['verified'] = True
        return value
    except Exception as error:
        # Never include authentication values or an untrusted remote error in a report.
        return {'verified': False, 'error_class': type(error).__name__}


def markdown(value):
    if not value.get('verified'):
        return '\n\n全球离线瓦片：未验证（' + value['error_class'] + '）。'
    lines = ['\n\n全球离线瓦片（MinIO 主机磁盘，与高程同盘；XYZ 从粗到细）',
             f"采样断点时间：{value['updated_at']}；任务状态：{value['phase']}；6 个进程；当前等级：{value['level']}。",
             '| 类型 | 当前级已落盘 / 总数 | 完成等级 | 累计瓦片 |',
             '| --- | ---: | --- | ---: |']
    for source, title in [('satellite', '卫星影像'), ('roadmap', '电子地图'), ('overlay', '透明道路')]:
        if source not in value['levels']:
            lines.append(f'| {title} | — | 尚未加入下载计划 | — |')
            continue
        levels = value['levels'][source]
        current = levels.get(str(value['level']), {'tiles': 0, 'total': 0})
        done = ', '.join(z for z, state in levels.items() if state['done']) or '—'
        lines.append(f"| {title} | {current['tiles']:,} / {current['total']:,} | {done} | {sum(s['tiles'] for s in levels.values()):,} |")
    lines.append(f"已落盘 {value['bytes'] / 1024**2:,.1f} MiB；目录：`{value['root']}`。")
    lines.append('数据源：Google 卫星(s) / 电子地图(m) / 透明道路(h)；不含 Earth 实景三维。网络：' + value.get('network', '未核定') + '。')
    errors = [error for levels in value['levels'].values() for state in levels.values() for error in state['errors']]
    if value.get('error') or errors:
        lines.append('下载错误：' + (value.get('error') or errors[0]))
    return '\n'.join(lines)

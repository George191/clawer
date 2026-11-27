"""Keep the existing workstation proxy reachable by the Google download on the terrain host."""
from pathlib import Path
import select
import socket
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import getproxies

import paramiko

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.offline_tile_progress import credentials


def main():
    proxy = urlsplit(getproxies()['https'])
    cells = credentials()
    with paramiko.SSHClient() as ssh:
        ssh.load_host_keys(str(Path.home() / '.ssh' / 'known_hosts'))
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        ssh.connect('192.168.20.61', username=cells['C3'], password=cells['C4'],
                    look_for_keys=False, allow_agent=False, timeout=10)
        transport = ssh.get_transport()
        transport.set_keepalive(20)

        def bridge(channel):
            try:
                with socket.create_connection((proxy.hostname, proxy.port), timeout=15) as sock:
                    while True:
                        readable, _, _ = select.select([channel, sock], [], [], 20)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (sock if source is channel else channel).sendall(data)
            finally:
                channel.close()

        def handler(channel, origin, address):
            threading.Thread(target=bridge, args=(channel,), daemon=True).start()

        port = transport.request_port_forward('127.0.0.1', 0, handler=handler)
        def run(command):
            _, stdout, stderr = ssh.exec_command(command, timeout=120)
            out, err = stdout.read().decode(), stderr.read().decode()
            if stdout.channel.recv_exit_status():
                raise RuntimeError(out + err)
            return out

        # Only update a verified clean Git checkout; no application code is copied to the host.
        print(run(f'''set -eu
cd /srv/terrain/tile-downloader-code
test -z "$(git status --porcelain)"
git -c http.proxy=http://127.0.0.1:{port} -c http.version=HTTP/1.1 pull --ff-only origin master
git rev-parse HEAD
sha256sum tools/download_global_tiles.py
mkdir -p /srv/terrain/imagery-global/output
'''), flush=True)
        run(f'''cd /srv/terrain/tile-downloader-code && nohup env HTTPS_PROXY=http://127.0.0.1:{port} HTTP_PROXY=http://127.0.0.1:{port} python3 -B tools/download_global_tiles.py --root /srv/terrain/imagery-global > /srv/terrain/imagery-global/output/download.log 2>&1 < /dev/null &
''')
        print('Google download started; workstation proxy tunnel must remain running.', flush=True)
        while transport.is_active():
            time.sleep(15)
            state = run("cat /srv/terrain/imagery-global/output/status.json 2>/dev/null || tail -n 5 /srv/terrain/imagery-global/output/download.log")
            print(state, flush=True)
            import json
            try:
                phase = json.loads(state)['phase']
            except (ValueError, KeyError):
                raise RuntimeError('Downloader did not publish a valid status: ' + state)
            if phase in ('complete', 'failed'):
                return
        raise RuntimeError('SSH proxy connection closed')


if __name__ == '__main__':
    main()

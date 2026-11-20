import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from .core import ManagedCore
from .manager import Manager
from .server import PANEL_URL, create_server


def wait_ready(socket_path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = subprocess.run(
            ['curl', '-qfsS', '--noproxy', '*', '--max-time', '1',
             '--unix-socket', str(socket_path), PANEL_URL + '/api/health'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            print(f'在电脑浏览器打开（通过 SSH 转发）\n{PANEL_URL}')
            return
        time.sleep(.2)
    raise SystemExit('PROXY 管理网页未在 20 秒内启动，请查看 tmux 会话日志')


def main():
    parser = argparse.ArgumentParser(description='PROXY — Ubuntu 网页代理管理')
    parser.add_argument('--proxy-port', type=int, default=17891)
    parser.add_argument('--controller-port', type=int, default=19091)
    parser.add_argument('--mihomo', default=str(Path.home() / 'bin/mihomo'))
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.local/share/proxy-panel')
    parser.add_argument('--wait-ready', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    args.data_dir = args.data_dir.expanduser().resolve()
    socket_path = args.data_dir / 'panel.sock'
    if args.wait_ready:
        wait_ready(socket_path)
        return
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (args.data_dir / 'panel.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('PROXY 已经在运行')
    if args.proxy_port == args.controller_port:
        raise SystemExit('代理和控制接口需要不同端口')
    core = ManagedCore(args.data_dir / 'core', args.mihomo, args.proxy_port, args.controller_port)
    manager = Manager(args.data_dir, core)
    server = None
    try:
        server = create_server(manager, socket_path)
        try:
            manager.start()
        except (ValueError, OSError) as e:
            core.last_error = str(e)
            print('代理启动失败，管理网页仍可使用：' + str(e), flush=True)
        def shutdown(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        print(f'PROXY 已启动\n管理套接字 {socket_path}\n通过 SSH 转发打开 {PANEL_URL}', flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        manager.close()
        lock.close()


if __name__ == '__main__':
    main()

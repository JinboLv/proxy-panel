from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socket
from socketserver import ThreadingMixIn, UnixStreamServer
import stat
from urllib.parse import unquote, urlsplit

from .nodes import MAX_IMPORT

STATIC = Path(__file__).parent / 'static'
PANEL_HOST = '127.0.0.1:18080'
PANEL_URL = f'http://{PANEL_HOST}'


class PanelServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True

    def __init__(self, path, handler):
        self.path = Path(path)
        self.socket_identity = None
        parent = self.path.parent.stat()
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError('管理套接字必须放在当前用户拥有且权限为 700 的目录里')
        if self.path.exists() or self.path.is_symlink():
            old = self.path.lstat()
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.geteuid():
                raise ValueError('管理套接字路径已被占用')
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(1)
                try:
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()
                else:
                    raise ValueError('管理套接字已在使用中')
        super().__init__(str(self.path), handler)

    def server_bind(self):
        super().server_bind()
        info = self.path.lstat()
        self.socket_identity = (info.st_dev, info.st_ino)
        self.path.chmod(0o600)

    def server_close(self):
        super().server_close()
        if self.socket_identity is not None:
            try:
                info = self.path.lstat()
                if (info.st_dev, info.st_ino) == self.socket_identity:
                    self.path.unlink()
            except FileNotFoundError:
                pass


def create_server(manager, path, browser_host=PANEL_HOST):
    browser_origin = f'http://{browser_host}'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'PROXY'

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_message(self, *_):
            pass

        def send(self, code, data, content_type='application/json; charset=utf-8'):
            body = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith('application/json') else data
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            try:
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def body(self):
            if self.headers.get('Content-Type', '').split(';')[0].lower() != 'application/json':
                raise ValueError('请使用 JSON 请求')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > MAX_IMPORT + 65536:
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError()
                return body
            except (ValueError, UnicodeError):
                raise ValueError('请求内容无效或超过大小限制') from None

        def route(self):
            path = unquote(urlsplit(self.path).path)
            method = self.command
            origin = self.headers.get('Origin')
            if (
                self.headers.get_all('Host') != [browser_host]
                or self.headers.get('Sec-Fetch-Site') not in {None, 'none', 'same-origin'}
                or (origin is not None and origin != browser_origin)
                or (method != 'GET' and origin != browser_origin)
            ):
                return self.send(403, {'error': '请通过 SSH 转发打开管理网页，跨站请求已拒绝'})
            if method == 'GET' and path in {'/', '/app.js', '/style.css'}:
                name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
                file = STATIC / name
                if not file.exists():
                    return self.send(404, {'error': '页面尚未就绪'})
                kind = {'index.html': 'text/html', 'app.js': 'application/javascript', 'style.css': 'text/css'}[name]
                return self.send(200, file.read_bytes(), kind + '; charset=utf-8')
            if method == 'GET' and path == '/api/health':
                return self.send(200, {'ok': True, 'app': 'proxy-panel'})
            data = self.body() if method in {'POST', 'PATCH'} else {}
            parts = path.strip('/').split('/')
            if path == '/api/state' and method == 'GET':
                return self.send(200, manager.snapshot())
            if path == '/api/access' and method == 'GET':
                return self.send(200, manager.core.access())
            if path == '/api/jobs' and method == 'GET':
                return self.send(200, {'jobs': manager.active_jobs()})
            if path == '/api/logs' and method == 'GET':
                return self.send(200, {'logs': manager.logs()})
            if path == '/api/nodes/import' and method == 'POST':
                return self.send(200, manager.import_nodes(data.get('text')))
            if len(parts) == 3 and parts[:2] == ['api', 'nodes']:
                if method == 'PATCH':
                    manager.edit_node(parts[2], data)
                    return self.send(200, {'ok': True})
                if method == 'DELETE':
                    manager.delete_node(parts[2])
                    return self.send(200, {'ok': True})
            if len(parts) == 4 and parts[:2] == ['api', 'nodes'] and parts[3] == 'select' and method == 'POST':
                manager.select(parts[2])
                return self.send(200, {'ok': True})
            if path == '/api/subscriptions' and method == 'POST':
                return self.send(200, manager.add_subscription(data.get('name'), data.get('url'), data.get('interval', 0), data.get('via_proxy', False)))
            if len(parts) == 3 and parts[:2] == ['api', 'subscriptions'] and method == 'DELETE':
                manager.delete_subscription(parts[2])
                return self.send(200, {'ok': True})
            if len(parts) == 4 and parts[:2] == ['api', 'subscriptions'] and parts[3] == 'refresh' and method == 'POST':
                return self.send(202, manager.submit('refresh', parts[2]))
            if path == '/api/tests' and method == 'POST':
                return self.send(202, manager.submit(data.get('kind'), data.get('ids')))
            if len(parts) == 3 and parts[:2] == ['api', 'jobs'] and method == 'GET':
                return self.send(200, manager.job(parts[2]))
            if len(parts) == 3 and parts[:2] == ['api', 'core'] and method == 'POST':
                manager.core_action(parts[2])
                return self.send(200, {'ok': True})
            return self.send(404, {'error': '未找到此页面或操作'})

        def dispatch(self):
            try:
                self.route()
            except (ValueError, TypeError, KeyError) as error:
                self.send(400, {'error': str(error) if isinstance(error, ValueError) else '请求参数无效'})
            except (TimeoutError, ConnectionError):
                self.send(408, {'error': '请求超时，请重试'})
            except Exception:
                self.send(500, {'error': '操作失败，原有配置已保留，请检查程序日志'})

        do_GET = dispatch
        do_POST = dispatch
        do_PATCH = dispatch
        do_DELETE = dispatch

    return PanelServer(path, Handler)

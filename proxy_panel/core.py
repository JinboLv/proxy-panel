import copy
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import getpass
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import yaml


def redact(text):
    text = re.sub(r'(?i)(?:https?|vless|vmess|trojan|ss|tuic|hy2|hysteria2)://[^\s\"\x27]+', '<链接已隐藏>', str(text))
    return re.sub(r'(?i)((?:password|uuid|secret|token)\s*[:=]\s*)[^\s,]+', r'\1<已隐藏>', text)


def build_config(nodes, selected, proxy_port, controller_port, secret, authentication=None):
    ordered = sorted(nodes, key=lambda n: n['id'] != selected)
    proxies = []
    for node in ordered:
        config = copy.deepcopy(node['config'])
        config['name'] = node['id']
        proxies.append(config)
    return {
        'mixed-port': proxy_port, 'bind-address': '127.0.0.1', 'allow-lan': False,
        'external-controller': f'127.0.0.1:{controller_port}', 'secret': secret,
        'authentication': authentication or [], 'skip-auth-prefixes': [],
        'mode': 'rule', 'log-level': 'info', 'ipv6': False,
        'profile': {'store-selected': False}, 'proxies': proxies,
        'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'proxies': [n['id'] for n in ordered]}] if proxies else [],
        'rules': ['DOMAIN,localhost,DIRECT', 'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve', 'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve', 'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve', 'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve', 'IP-CIDR6,::1/128,DIRECT,no-resolve', 'MATCH,PROXY' if proxies else 'MATCH,REJECT'],
    }


class ManagedCore:
    def __init__(self, data_dir, binary, proxy_port, controller_port):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.binary = str(binary)
        self.proxy_port, self.controller_port = proxy_port, controller_port
        if proxy_port == controller_port:
            raise ValueError('代理端口与控制端口不能相同')
        secret_path = self.data_dir / 'controller.secret'
        if not secret_path.exists():
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(secrets.token_urlsafe(32))
        self.secret = secret_path.read_text().strip()
        password_path = self.data_dir / 'proxy.secret'
        if not password_path.exists():
            fd = os.open(password_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_urlsafe(24))
        self.proxy_user = getpass.getuser()
        self.proxy_password = password_path.read_text().strip()
        self.process = None
        self.lock = threading.RLock()
        self.reader = None
        self.last_error = None
        self.opener = build_opener(ProxyHandler({}))
        self.log_path = self.data_dir / 'mihomo.log'

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def validate(self, nodes, selected):
        config = build_config(nodes, selected, self.proxy_port, self.controller_port, self.secret, [self.proxy_user + ':' + self.proxy_password])
        fd, name = tempfile.mkstemp(prefix='check-', suffix='.yaml', dir=self.data_dir)
        try:
            with os.fdopen(fd, 'w') as f:
                yaml.safe_dump(config, f, allow_unicode=True)
            result = subprocess.run([self.binary, '-t', '-d', str(self.data_dir), '-f', name], capture_output=True, text=True, timeout=12)
            if result.returncode:
                # Validation output may contain imported passwords; keep it private.
                raise ValueError('mihomo 未接受此配置，请检查节点协议、加密方式或 Reality 参数')
        finally:
            os.unlink(name)
        return config

    def _write_config(self, config):
        target = self.data_dir / 'config.yaml'
        fd, name = tempfile.mkstemp(prefix='config-', dir=self.data_dir)
        try:
            with os.fdopen(fd, 'w') as f:
                yaml.safe_dump(config, f, allow_unicode=True)
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _read_logs(self, process):
        handler = RotatingFileHandler(self.log_path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')
        try:
            for line in process.stdout:
                handler.emit(logging.LogRecord('mihomo', logging.INFO, '', 0, redact(line.rstrip()), (), None))
        finally:
            process.stdout.close()
            handler.close()

    def start(self, nodes, selected):
        with self.lock:
            if self.running:
                return
            for port in [self.proxy_port, self.controller_port]:
                try:
                    with socket.socket() as sock:
                        # Match mihomo's listening socket on Linux after a recent restart.
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind(('127.0.0.1', port))
                except OSError:
                    raise ValueError(f'端口 {port} 已被占用，未触碰占用它的进程') from None
            config = self.validate(nodes, selected)
            self._write_config(config)
            self.last_error = None
            owner_fd = os.pidfd_open(os.getpid())
            try:
                self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('core_child.py')), str(owner_fd), self.binary, '-d', str(self.data_dir)], pass_fds=(owner_fd,), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            finally:
                os.close(owner_fd)
            self.reader = threading.Thread(target=self._read_logs, args=(self.process,), daemon=True)
            self.reader.start()
            for _ in range(80):
                if not self.running:
                    break
                try:
                    self.api('/version')
                    if selected and nodes:
                        self.api('/proxies/PROXY', 'PUT', {'name': selected})
                    with socket.create_connection(('127.0.0.1', self.proxy_port), timeout=.2):
                        pass
                    return
                except (ValueError, OSError):
                    time.sleep(.05)
            self.stop()
            self.last_error = 'mihomo 启动失败，请查看运行日志'
            raise ValueError(self.last_error)

    def api(self, path, method='GET', payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(f'http://127.0.0.1:{self.controller_port}' + path, data=data, method=method,
                          headers={'Authorization': 'Bearer ' + self.secret, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=2) as response:
                raw = response.read(4 * 1024 * 1024)
                return json.loads(raw) if raw else {}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            raise ValueError('无法连接独立 mihomo 控制接口') from None

    def reload(self, nodes, selected):
        with self.lock:
            config = self.validate(nodes, selected)
            path = self.data_dir / 'config.yaml'
            old = path.read_bytes() if path.exists() else None
            self._write_config(config)
            if self.running:
                try:
                    self.api('/configs?force=true', 'PUT', {'path': str(path)})
                    if nodes and selected:
                        self.api('/proxies/PROXY', 'PUT', {'name': selected})
                except Exception:
                    if old is not None:
                        path.write_bytes(old)
                        self.api('/configs?force=true', 'PUT', {'path': str(path)})
                    raise

    def stop(self):
        with self.lock:
            if self.running:
                self.process.terminate()
                try:
                    self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            if self.reader:
                self.reader.join(timeout=2)

    def logs(self):
        if not self.log_path.exists():
            return ''
        with self.log_path.open('rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 50000))
            return redact(f.read().decode('utf-8', errors='replace'))

    def access(self):
        credentials = quote(self.proxy_user, safe='') + ':' + quote(self.proxy_password, safe='')
        address = f'{credentials}@127.0.0.1:{self.proxy_port}'
        return {'http': 'http://' + address, 'socks': 'socks5h://' + address, 'user': self.proxy_user}

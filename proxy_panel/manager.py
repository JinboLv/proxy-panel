import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .core import ManagedCore, redact
from .nodes import MAX_IMPORT, normalize, parse_nodes
from .store import Store

PROBE_LIMITS = {'delay': 8, 'speed': 2}


def identity(config):
    c = {k: v for k, v in config.items() if k != 'name'}
    return 'node-' + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:20]


def make_node(config, source=None):
    return {'id': identity(config), 'name': config['name'], 'config': config, 'source': source, 'sources': [source] if source else [], 'manual': source is None, 'result': None}


class Manager:
    def __init__(self, data_dir, core):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        self.store = Store(self.data_dir / 'state.json')
        if any('sources' not in n for n in self.store.snapshot()['nodes']):
            def migrate(data):
                for n in data['nodes']:
                    n.setdefault('sources', [n['source']] if n.get('source') else [])
                    n.setdefault('manual', n.get('source') is None)
            self.store.transaction(migrate)
        self.core = core
        self.lock = threading.RLock()
        self.jobs_lock = threading.RLock()
        self.jobs = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='proxy-panel-job')
        self.probe_executors = {
            kind: ThreadPoolExecutor(max_workers=limit, thread_name_prefix=f'proxy-panel-{kind}')
            for kind, limit in PROBE_LIMITS.items()
        }
        self.probe_launch_lock = threading.Lock()
        self.closing = threading.Event()
        self.scheduler = threading.Thread(target=self._schedule, daemon=True)
        self.scheduler.start()

    def _change(self, fn):
        with self.lock:
            old = self.store.snapshot()
            applied = False
            def transaction(data):
                nonlocal applied
                result = fn(data)
                ids = {n['id'] for n in data['nodes']}
                if data['selected'] not in ids:
                    data['selected'] = data['nodes'][0]['id'] if data['nodes'] else None
                self.core.validate(data['nodes'], data['selected'])
                if self.core.running:
                    self.core.reload(data['nodes'], data['selected'])
                    applied = True
                return result
            try:
                return self.store.transaction(transaction)
            except Exception:
                if applied:
                    self.core.reload(old['nodes'], old['selected'])
                raise

    def snapshot(self):
        data = self.store.snapshot()
        public_nodes = []
        for n in data['nodes']:
            c = n['config']
            public_nodes.append({'id': n['id'], 'name': n['name'], 'server': c['server'], 'port': c['port'], 'type': c['type'], 'network': c.get('network', 'tcp'), 'servername': c.get('servername', ''), 'security': 'Reality' if c.get('reality-opts') else ('TLS' if c.get('tls') else ''), 'source': n['source'], 'result': n.get('result')})
        subscriptions = []
        for s in data['subscriptions']:
            public = {k: v for k, v in s.items() if k != 'url'}
            public['url_display'] = urlsplit(s['url']).netloc
            public['count'] = sum(s['id'] in n['sources'] for n in data['nodes'])
            subscriptions.append(public)
        status = {'running': self.core.running, 'user': self.core.proxy_user, 'proxy_port': self.core.proxy_port, 'pid': self.core.process.pid if self.core.running else None, 'last_error': self.core.last_error, 'upload': 0, 'download': 0, 'connections': 0}
        if self.core.running:
            try:
                conns = self.core.api('/connections')
                status.update(upload=conns.get('uploadTotal', 0), download=conns.get('downloadTotal', 0), connections=len(conns.get('connections') or []))
            except ValueError:
                status['last_error'] = '控制接口暂时无法连接'
        return {'nodes': public_nodes, 'subscriptions': subscriptions, 'selected': data['selected'], 'core': status}

    def import_nodes(self, text):
        configs = parse_nodes(text)
        def mutate(data):
            count = self._append_configs(data, configs)
            return {'added': count, 'duplicates': len(configs) - count}
        return self._change(mutate)

    def _append_configs(self, data, configs, source=None, old_results=None):
        fingerprints = {identity(n['config']): n for n in data['nodes']}
        ids = {n['id'] for n in data['nodes']}
        added = 0
        for config in configs:
            fingerprint = identity(config)
            if fingerprint in fingerprints:
                existing = fingerprints[fingerprint]
                if source is None:
                    existing['manual'] = True
                elif source not in existing['sources']:
                    existing['sources'].append(source)
                existing['source'] = None if existing['manual'] else existing['sources'][0]
                continue
            node = make_node(config, source)
            if node['id'] in ids:
                node['id'] += '-' + secrets.token_hex(4)
            node['result'] = (old_results or {}).get(node['id'])
            data['nodes'].append(node)
            ids.add(node['id'])
            fingerprints[fingerprint] = node
            added += 1
        return added

    def _find_node(self, data, node_id):
        for n in data['nodes']:
            if n['id'] == node_id:
                return n
        raise ValueError('节点不存在，请刷新列表')

    def select(self, node_id):
        def mutate(data):
            self._find_node(data, node_id)
            data['selected'] = node_id
        self._change(mutate)

    def edit_node(self, node_id, fields):
        def mutate(data):
            n = self._find_node(data, node_id)
            for key in ['name', 'server', 'port', 'servername']:
                if key in fields:
                    n['config'][key] = fields[key]
            n['config'] = normalize(n['config'])
            n['name'] = n['config']['name']
            n['result'] = None
        self._change(mutate)

    def delete_node(self, node_id):
        def mutate(data):
            self._find_node(data, node_id)
            data['nodes'] = [n for n in data['nodes'] if n['id'] != node_id]
        self._change(mutate)

    def add_subscription(self, name, url, interval=0, via_proxy=False):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('请填写订阅名称')
        if not isinstance(url, str):
            raise ValueError('订阅地址无效')
        u = urlsplit(url)
        if u.scheme not in {'http', 'https'} or not u.hostname or u.username or u.password:
            raise ValueError('订阅地址必须是 HTTP 或 HTTPS 链接')
        if interval not in {0, 1, 6, 12, 24, 48, 168}:
            raise ValueError('更新间隔无效')
        sub = {'id': secrets.token_hex(10), 'name': name.strip()[:160], 'url': url.strip(), 'interval': interval, 'via_proxy': bool(via_proxy), 'created_at': time.time(), 'updated_at': None, 'checked_at': None, 'error': None}
        self.store.transaction(lambda d: d['subscriptions'].append(sub))
        return {'id': sub['id']}

    def _find_subscription(self, data, sub_id):
        for s in data['subscriptions']:
            if s['id'] == sub_id:
                return s
        raise ValueError('订阅不存在')

    def replace_subscription(self, sub_id, text):
        configs = parse_nodes(text)
        def mutate(data):
            sub = self._find_subscription(data, sub_id)
            old_results = {n['id']: n.get('result') for n in data['nodes'] if sub_id in n['sources']}
            self._remove_source(data, sub_id)
            added = self._append_configs(data, configs, sub_id, old_results)
            sub.update(updated_at=time.time(), checked_at=time.time(), error=None)
            return {'added': added}
        return self._change(mutate)

    def refresh_subscription(self, sub_id):
        sub = self._find_subscription(self.store.snapshot(), sub_id)
        try:
            if sub['via_proxy'] and not self.core.running:
                raise ValueError('请先启动代理，再通过当前节点更新订阅')
            with tempfile.TemporaryDirectory(prefix='subscription-', dir=self.data_dir) as directory:
                destination = Path(directory) / 'content'
                args = ['curl', '-q', '-f', '-sS', '-L', '--proto', '=http,https', '--proto-redir', '=http,https', '--connect-timeout', '5', '--max-time', '15', '--max-filesize', str(MAX_IMPORT), '--output', str(destination), '--user-agent', 'clash.meta/1.19 PROXY', '--config', '-']
                settings = 'url = ' + json.dumps(sub['url'], ensure_ascii=False) + '\n'
                if sub['via_proxy']:
                    args += ['--noproxy', '', '--proxy', f'http://127.0.0.1:{self.core.proxy_port}']
                    settings += 'proxy-user = ' + json.dumps(self.core.proxy_user + ':' + self.core.proxy_password) + '\n'
                else:
                    args += ['--noproxy', '*', '--proxy', '']
                process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
                try:
                    process.stdin.write(settings); process.stdin.close()
                    deadline = time.monotonic() + 16
                    while process.poll() is None:
                        if self.closing.wait(.05) or time.monotonic() > deadline:
                            raise ValueError('订阅更新已停止或超时')
                        if destination.exists() and destination.stat().st_size > MAX_IMPORT:
                            raise ValueError('订阅超过 4 MB')
                    if process.returncode:
                        raise ValueError('订阅下载失败，请检查地址、网络和更新方式')
                    raw = destination.read_bytes()
                    if len(raw) > MAX_IMPORT:
                        raise ValueError('订阅超过 4 MB')
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=1)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()
            return self.replace_subscription(sub_id, raw.decode('utf-8-sig'))
        except Exception as e:
            error = str(e) if isinstance(e, ValueError) else '订阅下载失败，请检查地址、网络和更新方式'
            def failed(data):
                for s in data['subscriptions']:
                    if s['id'] == sub_id:
                        s.update(checked_at=time.time(), error=redact(error))
            self.store.transaction(failed)
            raise ValueError(redact(error)) from None

    def delete_subscription(self, sub_id):
        def mutate(data):
            self._find_subscription(data, sub_id)
            data['subscriptions'] = [s for s in data['subscriptions'] if s['id'] != sub_id]
            self._remove_source(data, sub_id)
        self._change(mutate)

    def _remove_source(self, data, sub_id):
        keep = []
        for n in data['nodes']:
            n['sources'] = [s for s in n['sources'] if s != sub_id]
            if n['manual'] or n['sources']:
                n['source'] = None if n['manual'] else n['sources'][0]
                keep.append(n)
        data['nodes'] = keep

    def start(self):
        with self.lock:
            data = self.store.snapshot()
            self.core.start(data['nodes'], data['selected'])

    def core_action(self, action):
        with self.lock:
            if action not in {'start', 'stop', 'restart'}:
                raise ValueError('未知操作')
            if action in {'stop', 'restart'}:
                self.core.stop()
            if action in {'start', 'restart'}:
                self.start()

    def submit(self, kind, target):
        if kind not in {'delay', 'speed', 'refresh'}:
            raise ValueError('未知测试类型')
        data = self.store.snapshot()
        if kind == 'refresh':
            self._find_subscription(data, target)
        else:
            if not isinstance(target, list) or not 1 <= len(target) <= len(data['nodes']) or any(not isinstance(i, str) for i in target) or len(set(target)) != len(target):
                raise ValueError('请选择有效且不重复的节点')
            for node_id in target:
                self._find_node(data, node_id)
        with self.jobs_lock:
            if self.closing.is_set():
                raise ValueError('程序正在关闭，无法开始新任务')
            for j in self.jobs.values():
                if j['kind'] == kind and j.get('target') == target and j['status'] in {'queued', 'running'}:
                    return {'id': j['id']}
            if sum(j['status'] in {'queued', 'running'} for j in self.jobs.values()) >= 4:
                raise ValueError('已有多个任务正在执行，请稍后再试')
            done = [k for k, j in self.jobs.items() if j['status'] not in {'queued', 'running'}]
            for key in done[:-16]:
                del self.jobs[key]
            key = secrets.token_hex(10)
            self.jobs[key] = {'id': key, 'kind': kind, 'target': target, 'status': 'queued', 'done': 0, 'total': 1 if kind == 'refresh' else len(target), 'results': [], 'error': None}
            if kind in PROBE_LIMITS:
                self.jobs[key]['parallel_limit'] = min(PROBE_LIMITS[kind], len(target))
            self.executor.submit(self._run_job, key, kind, target)
            return {'id': key}

    def job(self, job_id):
        with self.jobs_lock:
            if job_id not in self.jobs:
                raise ValueError('任务不存在或已过期')
            return copy.deepcopy(self.jobs[job_id])

    def active_jobs(self):
        with self.jobs_lock:
            return copy.deepcopy([j for j in self.jobs.values() if j['status'] in {'queued', 'running'}])

    def _run_job(self, key, kind, target):
        with self.jobs_lock:
            self.jobs[key]['status'] = 'running'
        try:
            if kind == 'refresh':
                result = self.refresh_subscription(target)
                with self.jobs_lock:
                    self.jobs[key].update(done=1, results=[result])
            else:
                self._run_probes(key, kind, target)
            with self.jobs_lock:
                self.jobs[key]['status'] = 'done'
        except Exception as e:
            with self.jobs_lock:
                self.jobs[key].update(status='error', error=redact(str(e)) if isinstance(e, ValueError) else '任务执行失败，请重试')

    def _run_probes(self, key, kind, target):
        if self.closing.is_set():
            raise ValueError('程序正在关闭，任务已停止')
        nodes = {node['id']: node for node in self.store.snapshot()['nodes']}
        pool = self.probe_executors[kind]
        pending = {}
        try:
            for node_id in target:
                node = nodes.get(node_id)
                future = pool.submit(self._probe, node, kind == 'speed')
                pending[future] = (node_id, node)
            for future in as_completed(pending):
                if self.closing.is_set():
                    raise ValueError('程序正在关闭，任务已停止')
                node_id, node = pending[future]
                result = future.result()
                if node is not None:
                    self._save_probe_result(node, kind, result)
                with self.jobs_lock:
                    self.jobs[key]['results'].append({'id': node_id, **result})
                    self.jobs[key]['done'] += 1
        finally:
            for future in pending:
                future.cancel()

    def _probe(self, node, download):
        if self.closing.is_set():
            raise ValueError('程序正在关闭，任务已停止')
        if node is None:
            return {'at': time.time(), 'delay': None, 'error': '节点已删除'}
        return self._test_node(node, download)

    def _save_probe_result(self, node, kind, result):
        def save(data):
            for current in data['nodes']:
                if current['id'] == node['id'] and current['config'] == node['config']:
                    if kind == 'delay':
                        previous = current.get('result') or {}
                        for field in ('speed', 'speed_at'):
                            if field in previous:
                                result[field] = previous[field]
                    current['result'] = result
                    break
        self.store.transaction(save)

    def _test_node(self, node, download):
        result = {'at': time.time(), 'delay': None, 'error': None}
        try:
            with tempfile.TemporaryDirectory(prefix='proxy-panel-test-') as directory:
                core = None
                try:
                    # Serialize port handoff, then do network probes concurrently.
                    with self.probe_launch_lock:
                        if self.closing.is_set():
                            raise ValueError('程序正在关闭，测试已停止')
                        with socket.socket() as a, socket.socket() as b:
                            a.bind(('127.0.0.1', 0))
                            b.bind(('127.0.0.1', 0))
                            proxy_port, control_port = a.getsockname()[1], b.getsockname()[1]
                        core = ManagedCore(Path(directory), self.core.binary, proxy_port, control_port)
                        core.start([node], node['id'])
                    base = ['curl', '-q', '-sS', '--noproxy', '', '--proxy', f'http://127.0.0.1:{proxy_port}', '--connect-timeout', '8', '--max-time', '12', '-o', '/dev/null', '--config', '-']
                    credentials = 'proxy-user = ' + json.dumps(core.proxy_user + ':' + core.proxy_password) + '\n'
                    r = subprocess.run(base + ['-w', '%{http_code} %{time_total}', 'https://www.gstatic.com/generate_204'], input=credentials, capture_output=True, text=True, timeout=15)
                    parts = r.stdout.split()
                    if r.returncode or not parts or parts[0] != '204':
                        raise ValueError('连接失败或超时')
                    result['delay'] = round(float(parts[1]) * 1000)
                    if download:
                        if self.closing.is_set():
                            raise ValueError('程序正在关闭，测试已停止')
                        r = subprocess.run(base + ['--max-time', '20', '-w', '%{http_code} %{size_download} %{speed_download}', 'https://speed.cloudflare.com/__down?bytes=2097152'], input=credentials, capture_output=True, text=True, timeout=23)
                        parts = r.stdout.split()
                        if r.returncode or len(parts) != 3 or parts[0] != '200' or float(parts[1]) != 2097152:
                            raise ValueError('下载测试未完成，可能超时或被下载源拒绝')
                        result['speed'] = round(float(parts[2]))
                        result['speed_at'] = time.time()
                finally:
                    if core is not None:
                        core.stop()
        except Exception as e:
            result['error'] = str(e) if isinstance(e, ValueError) else '测试失败，请检查节点配置和网络'
            if download:
                result['speed'] = None
        return result

    def due_subscriptions(self, now):
        return [s for s in self.store.snapshot()['subscriptions'] if s.get('interval') and now - (s.get('checked_at') or s.get('created_at') or now) >= s['interval'] * 3600]

    def _schedule(self):
        while not self.closing.wait(30):
            now = time.time()
            for sub in self.due_subscriptions(now):
                try:
                    with self.jobs_lock:
                        busy = any(j['kind'] == 'refresh' and j['status'] in {'queued', 'running'} for j in self.jobs.values())
                    if not busy:
                        self.submit('refresh', sub['id'])
                except ValueError:
                    pass

    def close(self):
        with self.jobs_lock:
            self.closing.set()
        self.core.stop()
        self.executor.shutdown(wait=True, cancel_futures=True)
        for pool in self.probe_executors.values():
            pool.shutdown(wait=True, cancel_futures=True)

    def logs(self):
        text = self.core.logs()
        for n in self.store.snapshot()['nodes']:
            text = text.replace(n['id'], n['name'])
        return text

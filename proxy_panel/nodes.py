"""Parse share links without importing subscription-wide runtime settings."""
import base64
import json
import re
import uuid
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

TYPES = {'vless', 'vmess', 'trojan', 'ss', 'hysteria2', 'tuic', 'http', 'socks5'}
MAX_IMPORT = 4 * 1024 * 1024


def decode64(value):
    value = re.sub(r'\s+', '', value)
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True).decode('utf-8-sig')


def normalize(node):
    if not isinstance(node, dict) or node.get('type') not in TYPES:
        raise ValueError('不支持的节点类型，请使用 VLESS、VMess、Trojan、SS、Hysteria2 或 TUIC')
    try:
        n = json.loads(json.dumps(node, allow_nan=False))
        port = int(n['port'])
        server = n['server'].strip()
        if isinstance(n['port'], bool) or not 1 <= port <= 65535 or not server or re.search(r'[\s/@]', server):
            raise ValueError()
        if n['type'] in {'vless', 'vmess', 'tuic'}:
            n['uuid'] = str(uuid.UUID(n['uuid']))
        if n['type'] in {'trojan', 'ss', 'hysteria2', 'tuic'} and not n.get('password'):
            raise ValueError()
        if n['type'] == 'ss' and not n.get('cipher'):
            raise ValueError()
        n.update(server=server, port=port)
        n['name'] = re.sub(r'[\x00-\x1f\x7f]', '', str(n.get('name') or server)).strip()[:160] or server
    except (TypeError, KeyError, ValueError, OverflowError, RecursionError):
        raise ValueError('节点地址、端口、UUID 或密码无效') from None
    if n.get('network', 'tcp') not in {'tcp', 'ws', 'grpc', 'http', 'h2'}:
        raise ValueError('不支持此传输方式，请导入兼容 mihomo 的 YAML 节点')
    return n


def parse_link(raw):
    raw = raw.replace('\\://', '://').replace('\\:', ':')
    scheme = raw.split('://', 1)[0].lower()
    if scheme == 'vmess':
        try:
            o = json.loads(decode64(raw.split('://', 1)[1]))
            if o.get('tls', '') not in {'', 'none', 'tls'}:
                raise ValueError('未知的 VMess TLS 设置')
            n = {'name': o.get('ps'), 'type': 'vmess', 'server': o['add'], 'port': o['port'], 'uuid': o['id'], 'alterId': int(o.get('aid', 0)), 'cipher': o.get('scy', 'auto'), 'network': o.get('net', 'tcp'), 'udp': True}
            if o.get('tls') == 'tls':
                n.update(tls=True, servername=o.get('sni') or o.get('host') or o['add'], **{'client-fingerprint': o.get('fp', 'chrome')})
            if n['network'] == 'ws':
                n['ws-opts'] = {'path': o.get('path') or '/'}
                if o.get('host'):
                    n['ws-opts']['headers'] = {'Host': o['host']}
            if n['network'] == 'grpc':
                n['grpc-opts'] = {'grpc-service-name': o.get('path', '')}
            return normalize(n)
        except (KeyError, TypeError, ValueError):
            raise ValueError('VMess 链接格式无效') from None
    if scheme == 'ss':
        body = raw.split('://', 1)[1]
        if '@' not in body.split('#', 1)[0]:
            encoded, sep, fragment = body.partition('#')
            raw = 'ss://' + decode64(encoded) + (sep + fragment if sep else '')
    u = urlsplit(raw)
    q = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
    kind = {'hy2': 'hysteria2'}.get(scheme, scheme)
    if kind not in TYPES:
        raise ValueError('不支持此链接类型')
    try:
        n = {'name': unquote(u.fragment) or u.hostname, 'type': kind, 'server': u.hostname, 'port': u.port or (0 if ':0' in u.netloc else 443), 'udp': True}
    except ValueError:
        raise ValueError('节点端口无效') from None
    user = unquote(u.username or '')
    if kind in {'vless', 'tuic'}:
        n['uuid'] = user
    if kind in {'trojan', 'hysteria2'}:
        n['password'] = user + (':' + unquote(u.password) if u.password is not None else '')
    if kind == 'tuic':
        n['password'] = unquote(u.password or '')
        n['congestion-controller'] = q.get('congestion_control', 'cubic')
    if kind == 'ss':
        auth = user + ':' + unquote(u.password) if u.password is not None else decode64(user)
        if ':' not in auth:
            raise ValueError('Shadowsocks 密码格式无效')
        n['cipher'], n['password'] = auth.split(':', 1)
        if q.get('plugin'):
            pieces = q['plugin'].split(';')
            plugin = pieces[0]
            if plugin not in {'obfs-local', 'simple-obfs', 'v2ray-plugin'}:
                raise ValueError('暂不支持此 Shadowsocks 插件')
            opts = dict(p.split('=', 1) if '=' in p else (p, True) for p in pieces[1:])
            if plugin in {'obfs-local', 'simple-obfs'}:
                n.update(plugin='obfs', **{'plugin-opts': {'mode': opts.get('obfs', 'http'), 'host': opts.get('obfs-host', '')}})
            else:
                n.update(plugin=plugin, **{'plugin-opts': opts})
    if kind in {'http', 'socks5'}:
        if user:
            n['username'] = user
        if u.password:
            n['password'] = unquote(u.password)
    if kind not in {'ss', 'http', 'socks5'}:
        n['network'] = q.get('type', 'tcp')
        security = q.get('security', 'tls' if kind in {'trojan', 'hysteria2', 'tuic'} else 'none')
        if security not in {'none', 'tls', 'reality'}:
            raise ValueError('未知的安全设置；仅支持 none、tls 或 reality')
        if security in {'tls', 'reality'} or kind in {'trojan', 'hysteria2', 'tuic'}:
            n.update(tls=True, servername=q.get('sni') or q.get('peer') or u.hostname)
            n['client-fingerprint'] = q.get('fp', 'chrome')
        if q.get('insecure') in {'1', 'true'} or q.get('allowInsecure') in {'1', 'true'}:
            n['skip-cert-verify'] = True
        if q.get('alpn'):
            n['alpn'] = q['alpn'].split(',')
        if q.get('flow'):
            n['flow'] = q['flow']
        if security == 'reality':
            if not q.get('pbk'):
                raise ValueError('Reality 链接缺少公钥')
            n['reality-opts'] = {'public-key': q['pbk'], 'short-id': q.get('sid', '')}
            if q.get('support-x25519mlkem768') == 'true':
                n['reality-opts']['support-x25519mlkem768'] = True
        if n['network'] == 'ws':
            n['ws-opts'] = {'path': q.get('path', '/')}
            if q.get('host'):
                n['ws-opts']['headers'] = {'Host': q['host']}
        if n['network'] == 'grpc':
            n['grpc-opts'] = {'grpc-service-name': q.get('serviceName', q.get('path', ''))}
        if kind == 'hysteria2' and q.get('obfs'):
            n['obfs'] = q['obfs']
            n['obfs-password'] = q.get('obfs-password', '')
    return normalize(n)


def parse_nodes(text, _decoded=False):
    if not isinstance(text, str) or len(text.encode()) > MAX_IMPORT:
        raise ValueError('导入内容过大，最多 4 MB')
    text = text.strip().lstrip('\ufeff')
    if not text:
        raise ValueError('没有可导入的节点')
    try:
        if re.search(r'(?m)^\s*(proxies:|\[|\{|-\s*name:)', text):
            if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(text)):
                raise ValueError('为限制订阅资源占用，不支持 YAML 锚点或别名；请展开后导入')
            obj = yaml.safe_load(text)
            nodes = obj.get('proxies') if isinstance(obj, dict) else obj
            if not isinstance(nodes, list) or not nodes:
                raise ValueError('订阅中没有可导入的节点')
            if len(nodes) > 500:
                raise ValueError('单次最多导入 500 个节点')
            return [normalize(n) for n in nodes]
        if '://' not in text and '\\://' not in text and not _decoded:
            return parse_nodes(decode64(text), True)
        lines = [s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')]
        if not lines or len(lines) > 500:
            raise ValueError('单次需要 1–500 个节点')
        return [parse_link(s) for s in lines]
    except (yaml.YAMLError, UnicodeError, RecursionError, TypeError):
        raise ValueError('内容不是有效的节点链接、Base64 订阅或 YAML 节点') from None
    except ValueError as e:
        if not str(e) or 'base64' in str(e).lower() or 'padding' in str(e).lower() or 'Invalid' in str(e):
            raise ValueError('节点或订阅格式无效') from None
        raise

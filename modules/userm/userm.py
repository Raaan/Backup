#!/usr/bin/python3 -I
"""Standalone, root-local VLESS user management. No statistics dependency."""
import argparse
import base64
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from contextlib import ExitStack

VERSION = '1.0.1'
STATE = Path('/var/lib/userm')
SETTINGS = Path('/etc/userm/settings.json')


def run(args):
    # Root commands must not resolve tools through a caller-controlled PATH.
    environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
    result = subprocess.run(args, capture_output=True, text=True, timeout=40,
                            stdin=subprocess.DEVNULL, env=environment)
    if result.returncode:
        # Tool diagnostics can contain credentials. Do not print them.
        raise RuntimeError('Command failed: ' + Path(args[0]).name)
    return result.stdout


def load(path):
    return json.loads(Path(path).read_text())


def encoded(data):
    return (json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private_directory(path):
    path = Path(path)
    if path.is_symlink(): raise RuntimeError('私有目录不能是符号链接')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != 0: raise RuntimeError('私有目录必须属于 root')
    path.chmod(0o700)


def trusted_binary(path):
    # Do not execute a non-root service owner's executable as root.
    resolved = Path(path).resolve(strict=True)
    for entry in [resolved, *resolved.parents]:
        info = entry.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Xray 可执行文件路径必须由 root 独占写入')


def atomic(path, data, mode=0o600):
    path = Path(path)
    owner = path.stat() if path.exists() else None
    fd, temp = tempfile.mkstemp(prefix='.userm-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            if owner: os.fchown(stream.fileno(), owner.st_uid, owner.st_gid)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def valid_email(email):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9@.,_+\-]{0,127}', email):
        raise ValueError('email/用户名须为 1–128 个 ASCII 字符，不含空白或控制字符')
    return email


def public_key(private):
    """RFC 7748 X25519 base-point multiplication; private key stays in RAM."""
    raw = bytearray(base64.urlsafe_b64decode(private+'='*((-len(private)) % 4)))
    if len(raw) != 32: raise ValueError('Invalid REALITY key')
    raw[0] &= 248; raw[31] &= 127; raw[31] |= 64
    scalar = int.from_bytes(raw, 'little'); p = 2**255-19
    x1 = 9; x2, z2, x3, z3, swap = 1, 0, 9, 1, 0
    for t in range(254, -1, -1):
        bit = (scalar >> t) & 1; swap ^= bit
        if swap: x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a = (x2+z2) % p; aa = a*a % p
        b = (x2-z2) % p; bb = b*b % p; e = (aa-bb) % p
        c = (x3+z3) % p; d = (x3-z3) % p
        da = d*a % p; cb = c*b % p
        x3 = (da+cb)**2 % p; z3 = x1*(da-cb)**2 % p
        x2 = aa*bb % p; z2 = e*(aa+121665*e) % p
    if swap: x2, x3, z2, z3 = x3, x2, z3, z2
    value = (x2*pow(z2, p-2, p) % p).to_bytes(32, 'little')
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


class Xray:
    def __init__(self, unit, binary, paths, directory=None):
        self.unit, self.binary = unit, binary
        self.paths, self.directory = paths, directory

    def endpoints(self):
        result = []
        for path in self.paths:
            data = load(path)
            for index, inbound in enumerate(data.get('inbounds', [])):
                if inbound.get('protocol') == 'vless':
                    result.append({'adapter': self, 'path': path, 'resolved': path.resolve(), 'index': index, 'digest': sha(path.read_bytes()),
                                   'inbound': inbound})
        return result

    def validate(self, changes, staging):
        trusted_binary(self.binary)
        if self.directory:
            destination = staging / self.unit
            shutil.copytree(self.directory, destination)
            for path in self.paths:
                resolved = path.resolve()
                if resolved in changes:
                    target = destination / path.relative_to(self.directory)
                    # Never follow a copied config symlink back to production.
                    if target.is_symlink(): target.unlink()
                    target.write_bytes(changes[resolved])
            run([self.binary, 'run', '-test', '-confdir', str(destination)])
        else:
            args = [self.binary, 'run', '-test']
            for index, path in enumerate(self.paths):
                target = staging / (self.unit+str(index)+'.json')
                target.write_bytes(changes.get(path.resolve(), path.read_bytes()))
                args += ['-config', str(target)]
            run(args)


def discover():
    adapters, notices = [], []
    units = run(['systemctl', 'list-units', '--type=service', '--state=running',
                 '--no-legend', '--plain', '--no-pager'])
    for line in units.splitlines():
        if not line.strip(): continue
        unit = line.split()[0]
        pid = run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip()
        try:
            binary = str(Path('/proc', pid, 'exe').resolve(strict=True))
            args = Path('/proc', pid, 'cmdline').read_bytes().decode().split('\0')
        except (OSError, UnicodeError): continue
        if 'hysteria' in Path(binary).name:
            notices.append(unit+': HY2 已检测，1.0 仅支持 Xray VLESS；未修改')
        if Path(binary).name != 'xray': continue
        directory = None; paths = []
        cwd = Path('/proc', pid, 'cwd').resolve()
        for n, arg in enumerate(args[:-1]):
            if arg not in ('-config', '--config', '-c', '-confdir', '--confdir'): continue
            path = Path(args[n+1])
            if not path.is_absolute(): path = cwd / path
            if 'confdir' in arg:
                if directory is not None: raise RuntimeError('Multiple confdir unsupported')
                directory = path
                paths.extend(sorted(path.glob('*.json')))
            else: paths.append(path)
        if not paths:
            notices.append(unit+': 无可识别 JSON 配置路径；未修改'); continue
        adapter = Xray(unit, binary, paths, directory)
        # Fail visibly on unsupported JSON rather than silently omit a service.
        adapter.endpoints()
        adapters.append(adapter)
    endpoints = [e for a in adapters for e in a.endpoints()]
    return adapters, endpoints, notices


def users(endpoint):
    clients = endpoint['inbound'].get('settings', {}).get('clients', [])
    ids = [c['id'] for c in clients]; emails = [c.get('email', '') for c in clients]
    if len(set(ids)) != len(ids) or len(set(emails)) != len(emails) or not all(emails):
        raise RuntimeError('配置用户 UUID/email 缺失或重复；请先核对该入站')
    return clients


def identity_group(selected, endpoints):
    """Manage exact UUID mirrors together; refuse ambiguous partial overlap."""
    wanted = {c['id'] for c in users(selected)}
    if not wanted: raise RuntimeError('空入站无法推断共享身份；需手动配置')
    group = []
    for endpoint in endpoints:
        ids = {c['id'] for c in users(endpoint)}
        if ids & wanted and ids != wanted:
            raise RuntimeError('发现部分重叠 UUID；拒绝猜测用户同步关系')
        if ids == wanted: group.append(endpoint)
    return group


def title(endpoint):
    i = endpoint['inbound']; stream = i.get('streamSettings', {})
    return f"{stream.get('network', 'tcp')}/{stream.get('security', 'none')} {i.get('port')} [{endpoint['adapter'].unit}:{i.get('tag', '')}]"


def connection(endpoint, client, settings):
    inbound = endpoint['inbound']; stream = inbound.get('streamSettings', {})
    key = endpoint['adapter'].unit+':'+str(inbound.get('tag', ''))
    options = settings.get('endpoints', {}).get(key, {})
    host = options.get('host', settings.get('host'))
    if not host: raise ValueError('先运行 userm configure，设置本机公开地址')
    host = host.strip('[]')
    authority = '['+host+']' if ':' in host else host
    query = {'encryption': 'none', 'type': stream.get('network', 'tcp'),
             'security': stream.get('security', 'none')}
    if client.get('flow'): query['flow'] = client['flow']
    if query['security'] == 'reality':
        reality = stream['realitySettings']
        query.update({'sni': options.get('sni', reality['serverNames'][0]),
                      'pbk': public_key(reality['privateKey']),
                      'sid': reality.get('shortIds', [''])[0], 'fp': 'chrome'})
    elif query['security'] == 'tls':
        query['sni'] = options.get('sni', host)
        query['fp'] = 'chrome'
        alpn = stream.get('tlsSettings', {}).get('alpn')
        if alpn: query['alpn'] = ','.join(alpn)
    if query['type'] == 'xhttp':
        xhttp = stream.get('xhttpSettings', {})
        query['path'] = xhttp.get('path', '/')
        query['mode'] = xhttp.get('mode', 'auto')
        if xhttp.get('host'): query['host'] = xhttp['host']
    elif query['type'] == 'grpc':
        query['serviceName'] = stream.get('grpcSettings', {}).get('serviceName', '')
    elif query['type'] != 'tcp':
        raise ValueError('此传输尚未支持链接导出')
    return 'vless://'+urllib.parse.quote(client['id'], safe='')+'@'+authority+':'+str(inbound['port'])+'?'+urllib.parse.urlencode(query)+'#'+urllib.parse.quote(client['email'], safe='')


def plan(selected, endpoints, action, email=None, identity=None):
    group = identity_group(selected, endpoints)
    data = {}; original = {}
    if action == 'add':
        valid_email(email)
        identity = str(uuid.uuid4())
        for endpoint in group:
            if any(c['email'].casefold() == email.casefold() for c in users(endpoint)):
                raise ValueError('用户已存在；未作修改')
    elif action == 'delete':
        if not any(c['id'] == identity for c in users(selected)):
            raise ValueError('用户不存在；未作修改')
        if len(users(selected)) <= 1: raise ValueError('拒绝删除最后一个用户')
    else: raise ValueError('Unknown action')
    for endpoint in group:
        path = endpoint['path'].resolve()
        if path != endpoint['resolved']: raise RuntimeError('配置链接已切换；请重新扫描')
        if path not in data:
            original[path] = path.read_bytes(); data[path] = json.loads(original[path])
        if sha(original[path]) != endpoint['digest']:
            raise RuntimeError('配置已变化；请重新扫描')
        clients = data[path]['inbounds'][endpoint['index']]['settings']['clients']
        if action == 'add':
            new = copy.deepcopy(clients[0])
            new.update(id=identity, email=email)
            # Credentials and identity-specific metadata must not be inherited.
            new = {k: v for k, v in new.items() if k in ('id', 'email', 'flow', 'level')}
            clients.append(new)
        else:
            clients[:] = [c for c in clients if c['id'] != identity]
    return group, original, {p: encoded(v) for p, v in data.items()}


def lock_paths():
    return [STATE/'mutation.lock', Path('/var/lib/xray-xhttp443/control.lock'),
            Path('/var/lock/xray-usage.lock')]


def transaction(group, original, changes):
    private_directory(STATE)
    adapters = {e['adapter'].unit: e['adapter'] for e in group}
    with ExitStack() as stack:
        # Match the existing XHTTP controller's lock order. No DB access.
        for path in lock_paths():
            if not path.parent.exists(): continue
            handle = stack.enter_context(path.open('a'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for pending in STATE.glob('backup-*/journal.json'):
            if load(pending).get('status') in ('applying', 'rollback-needs-review'):
                raise RuntimeError('存在未完成变更，先检查私有备份：'+str(pending.parent))
        if any(p.read_bytes() != content for p, content in original.items()):
            raise RuntimeError('配置已变化；请重新扫描')
        if any(e['path'].resolve() != e['resolved'] for e in group):
            raise RuntimeError('配置链接已切换；请重新扫描')
        with tempfile.TemporaryDirectory(prefix='userm-') as temp:
            os.chmod(temp, 0o700)
            for adapter in adapters.values(): adapter.validate(changes, Path(temp))
        backup = STATE / ('backup-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(mode=0o700)
        journal = {'status': 'prepared', 'units': sorted(adapters), 'files': []}
        for index, (path, content) in enumerate(original.items()):
            saved = backup / str(index); atomic(saved, content)
            journal['files'].append({'path': str(path), 'backup': str(index),
                                     'before': sha(content), 'after': sha(changes[path]),
                                     'mode': path.stat().st_mode & 0o777})
        atomic(backup/'journal.json', encoded(journal))
        journal['status'] = 'applying'; atomic(backup/'journal.json', encoded(journal))
        try:
            for record in journal['files']:
                path = Path(record['path'])
                if sha(path.read_bytes()) != record['before']: raise RuntimeError('配置并发变化')
                atomic(path, changes[path], record['mode'])
            for unit in sorted(adapters): run(['systemctl', 'restart', unit])
            for unit in adapters: run(['systemctl', 'is-active', '--quiet', unit])
            journal['status'] = 'committed'
        except BaseException:
            conflict = False
            for record in journal['files']:
                path = Path(record['path']); current = sha(path.read_bytes())
                if current == record['after']:
                    atomic(path, (backup/record['backup']).read_bytes(), record['mode'])
                elif current != record['before']: conflict = True
            if not conflict:
                for unit in sorted(adapters):
                    try: run(['systemctl', 'restart', unit])
                    except Exception: conflict = True
            journal['status'] = 'rollback-needs-review' if conflict else 'rolled-back'
            atomic(backup/'journal.json', encoded(journal))
            raise RuntimeError('变更失败；恢复状态：'+journal['status']+'；备份：'+str(backup)) from None
        atomic(backup/'journal.json', encoded(journal))
        print('配置已生效；服务运行。备份：'+str(backup))
        print('尚需客户端验证连接；模块未测试公网连接。')


def choose(count, prompt='选择（0 返回）：'):
    value = input(prompt).strip()
    if value == '0': return None
    if not value.isdigit() or not 1 <= int(value) <= count: raise ValueError('无效选择')
    return int(value)-1


def display_users(endpoint, settings):
    for client in users(endpoint):
        print(client['email'])
        try: print('导入链接：'+connection(endpoint, client, settings))
        except (KeyError, ValueError): print('导入链接不可用；请检查本机地址及入站配置。')


def menu():
    while True:
        try:
            _, endpoints, notices = discover()
            visible = [e for e in endpoints if e['inbound'].get('listen') not in ('127.0.0.1', '::1')]
            print('\nuserm '+VERSION+' — 当前代理服务')
            for notice in notices: print(notice)
            for index, endpoint in enumerate(visible, 1): print(index, title(endpoint))
            selected = choose(len(visible), '选择服务（0 退出）：')
            if selected is None: return
            endpoint = visible[selected]
            print('\n'+title(endpoint)+'\n1 查看用户（全部连接）\n2 增加用户\n3 管理（删除用户）')
            action = choose(3)
            if action is None: continue
            settings = load(SETTINGS) if SETTINGS.exists() else {}
            clients = users(endpoint)
            if action == 0:
                display_users(endpoint, settings)
                continue
            if action == 1:
                email = input('新增 email/用户名：').strip()
                group, original, changes = plan(endpoint, endpoints, 'add', email=email)
            else:
                for n, c in enumerate(clients, 1): print(n, c['email'])
                index = choose(len(clients))
                if index is None: continue
                group, original, changes = plan(endpoint, endpoints, 'delete', identity=clients[index]['id'])
            print('共享 UUID 的关联入站将一并变更：')
            for peer in group: print('  '+title(peer))
            print('将备份配置，并短暂重启这些服务：'+', '.join(sorted({e['adapter'].unit for e in group})))
            print('统计可能因重启产生缺口；不改 fused/SQLite、证书或网络。')
            if input('输入 APPLY 确认，其他输入取消：').strip() != 'APPLY': continue
            transaction(group, original, changes)
            if action == 1:
                _, refreshed, _ = discover()
                current = next(e for e in refreshed if e['path'] == endpoint['path'] and e['index'] == endpoint['index'])
                client = next(c for c in users(current) if c['email'] == email)
                try: print(connection(current, client, settings))
                except (KeyError, ValueError): print('用户已配置；请运行 userm configure 后查看连接。')
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            # Raw exceptions can include JSON credentials or parser snippets.
            print('操作未完成；请检查配置、并发操作和 /var/lib/userm 私有变更记录。')
            if isinstance(error, RuntimeError): print(str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=['menu', 'scan', 'configure', 'version'], default='menu')
    args = parser.parse_args()
    if args.command == 'version': print(VERSION); return
    if os.geteuid() != 0: raise SystemExit('请使用 sudo userm')
    if args.command == 'configure':
        host = input('本机公开 IP 或域名（不含端口）：').strip().strip('[]')
        if not re.fullmatch(r'[A-Za-z0-9.:\-]+', host): raise SystemExit('无效地址')
        private_directory(SETTINGS.parent)
        settings = load(SETTINGS) if SETTINGS.exists() else {}
        settings['host'] = host
        atomic(SETTINGS, encoded(settings))
        print('已保存；可在 '+str(SETTINGS)+' 为每个 unit:tag 覆盖 host/sni。')
    elif args.command == 'scan':
        _, endpoints, notices = discover()
        for endpoint in endpoints: print(title(endpoint), 'users='+str(len(users(endpoint))))
        for notice in notices: print(notice)
    else: menu()


if __name__ == '__main__':
    try: main()
    except (EOFError, KeyboardInterrupt): print('\n已退出。')

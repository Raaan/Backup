#!/usr/bin/env python3
"""Run an administrator-supplied renewal client, validate, install, restart TLS consumers."""
import ipaddress
import json
import os
import pathlib
import subprocess
import tempfile
c=json.loads(pathlib.Path('/etc/la-portal/renew.json').read_text())
subprocess.run(c['argv'],check=True)
subprocess.run(['openssl','verify','-CAfile','/etc/ssl/certs/ca-certificates.crt','-untrusted',c['cert'],c['cert']],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['openssl','x509','-in',c['cert'],'-noout','-checkend','86400'],check=True,stdout=subprocess.DEVNULL)
try:ipaddress.ip_address(c['tls_name']);flag='-checkip'
except ValueError:flag='-checkhost'
subprocess.run(['openssl','x509','-in',c['cert'],'-noout',flag,c['tls_name']],check=True,stdout=subprocess.DEVNULL)
public=subprocess.check_output(['openssl','x509','-in',c['cert'],'-pubkey','-noout'])
private=subprocess.check_output(['openssl','pkey','-in',c['key'],'-pubout'])
if public!=private:raise RuntimeError('Certificate/key mismatch')
base=pathlib.Path('/etc/xray-xhttp443/cert-current')
pairs=[(pathlib.Path(c['cert']),base/'fullchain.pem'),(pathlib.Path(c['key']),base/'key.pem')]
old={dst:dst.read_bytes() for src,dst in pairs}
def atomic(dst,data):
    fd,tmp=tempfile.mkstemp(dir=dst.parent,prefix='.renew-')
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,dst)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
changed=any(src.read_bytes()!=old[dst] for src,dst in pairs)
if changed:
    try:
        for src,dst in pairs:atomic(dst,src.read_bytes())
        for unit in ('xray-xhttp443.service','hy2-udp443.service'):
            subprocess.run(['systemctl','restart',unit],check=True)
    except BaseException:
        for dst,data in old.items():atomic(dst,data)
        for unit in ('xray-xhttp443.service','hy2-udp443.service'):
            subprocess.run(['systemctl','restart',unit],check=False)
        raise
    print('Renewed certificate installed')
else:print('Certificate unchanged')

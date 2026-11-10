#!/usr/bin/env python3
"""LA Portal fresh-server installer. Plan is read-only; install needs --apply."""
import argparse
import base64
import datetime
import hashlib
import ipaddress
import json
import os
import pathlib
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
ARTIFACTS = {
 'xray': ('https://github.com/XTLS/Xray-core/releases/download/v26.10.31/Xray-linux-64.zip', 'e4bd9119f50ae7c41658b1abd0679ae06f98bd864a2907ff33f2f2cf7081b214'),
 'hysteria': ('https://github.com/HyNetworks/hysteria/releases/download/app/v2.13.0/hysteria-linux-amd64', '907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691'),
}
UNITS = ['xray.service','xray-xhttp443.service','hy2-udp443.service','la-usage.timer','la-monthly.timer','la-weekly.timer','la-renew.timer']

def read_config(path):
    c = json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))
    for field in ('host','tls_name','reality_sni','reality_target'):
        if not isinstance(c.get(field), str) or not c[field] or '__' in c[field] or any(ch.isspace() for ch in c[field]):
            raise ValueError('Fill field: '+field)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', c.get('server_id', '')):
        raise ValueError('Set a unique server_id')
    people = c.get('users', [])
    if not people or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', p) for p in people):
        raise ValueError('users must be explicit canonical ASCII account names')
    if len({p.casefold() for p in people}) != len(people):
        raise ValueError('Duplicate user names')
    cert = c.get('certificate', {'mode':'import'})
    if cert.get('mode') not in ('acme','import'):
        raise ValueError('certificate.mode must be acme or import')
    if cert.get('mode') == 'acme':
        if not isinstance(cert.get('email'),str) or '@' not in cert['email'] or any(ch.isspace() for ch in cert['email']):
            raise ValueError('Set certificate.email')
        if cert.get('agree_tos') is not True:
            raise ValueError('Accept CA subscriber terms explicitly in setup')
    else:
        for k in ('tls_cert','tls_key'):
            if not pathlib.Path(c[k]).is_file(): raise ValueError('Missing local certificate material: '+k)
    if c.get('mail'):
        raise ValueError('Mail is a separate module: run portal.py mail-setup --apply after base installation')
    renew = c.get('certificate_renewal_command')
    if renew is not None and (not isinstance(renew,list) or not renew or not all(isinstance(x,str) and x for x in renew) or not pathlib.PurePosixPath(renew[0]).is_absolute()):
        raise ValueError('Renewal command must be an explicit argv list with absolute executable')
    from modules.usage.v1_core.billing import normalize
    c['billing'] = normalize(c.get('billing',{}))
    return c

def unit(description, command):
    return f'''[Unit]
Description={description}
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=root
UMask=0077
ExecStart={command}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
'''

def stage(c, destination):
    d = pathlib.Path(destination)
    d.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = {}
    def put(path, value, mode=0o600):
        p = d/path.lstrip('/')
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if isinstance(value,dict): value=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
        if isinstance(value,str): value=value.encode('utf-8')
        p.write_bytes(value);p.chmod(mode)
        files[path] = {'sha256':hashlib.sha256(value).hexdigest(),'mode':mode}
    def copy(src,target,mode=0o600):
        data=(ROOT/src).read_bytes()
        if src.endswith('.py'): data=data.replace(b'fair-light-1',c['server_id'].encode('ascii'))
        put(target,data,mode)
    from modules.usage.v1_core.billing import normalize, calendar
    billing = normalize(c.get('billing',{}))
    put('/etc/la-portal/billing.json',billing)
    clients = [{'id':str(uuid.uuid4()),'email':'California,'+p+'-vless_reality_vision','level':0} for p in c['users']]
    private = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('=')
    sid = secrets.token_hex(8)
    def common(api):
        return {'log':{'loglevel':'warning'},'stats':{},'api':{'tag':'api','services':['StatsService']},
          'policy':{'levels':{'0':{'statsUserUplink':True,'statsUserDownlink':True}}},
          'inbounds':[{'tag':'api-in','listen':'127.0.0.1','port':api,'protocol':'dokodemo-door','settings':{'address':'127.0.0.1'}}],
          'outbounds':[{'tag':'direct','protocol':'freedom'}],
          'routing':{'rules':[{'type':'field','inboundTag':['api-in'],'outboundTag':'api'}]}}
    main=common(10085)
    for port in (443,):
        main['inbounds'].append({'tag':'REALITY'+str(port),'listen':'::','port':port,'protocol':'vless',
          'settings':{'clients':[dict(u,flow='xtls-rprx-vision') for u in clients],'decryption':'none'},
          'streamSettings':{'network':'tcp','security':'reality','realitySettings':{'show':False,'target':c['reality_target'],
            'serverNames':[c['reality_sni']],'privateKey':private,'shortIds':[sid]}}})
    main['inbounds'].append({'tag':'GRPC-LOCAL','listen':'127.0.0.1','port':31305,'protocol':'vless',
      'settings':{'clients':clients,'decryption':'none'},'streamSettings':{'network':'grpc','grpcSettings':{'serviceName':'portal'}}})
    xhttp=common(10086)
    xhttp['inbounds'].append({'tag':'XHTTP443','listen':'::','port':8443,'protocol':'vless',
      'settings':{'clients':clients,'decryption':'none'},'streamSettings':{'network':'xhttp','security':'tls',
      'xhttpSettings':{'path':'/'+secrets.token_hex(16),'mode':'packet-up'},
      'tlsSettings':{'alpn':['h2'],'certificates':[{'certificateFile':'/etc/xray-xhttp443/cert-current/fullchain.pem','keyFile':'/etc/xray-xhttp443/cert-current/key.pem'}]}}})
    put('/etc/v2ray-agent/xray/conf/portal.json',main)
    put('/etc/xray-xhttp443/current/config.json',xhttp)
    put('/etc/xray-xhttp443/cert-current/fullchain.pem',pathlib.Path(c['tls_cert']).read_bytes())
    put('/etc/xray-xhttp443/cert-current/key.pem',pathlib.Path(c['tls_key']).read_bytes())
    put('/etc/xray-xhttp443/collection-enabled','')
    passwords = {p:secrets.token_urlsafe(24) for p in c['users']}
    hy2={'listen':':443','tls':{'cert':'/etc/xray-xhttp443/cert-current/fullchain.pem','key':'/etc/xray-xhttp443/cert-current/key.pem'},
      'auth':{'type':'userpass','userpass':passwords},'trafficStats':{'listen':'127.0.0.1:10087','secret':secrets.token_urlsafe(32)},
      'masquerade':{'type':'string','string':{'content':'Service available.\n','statusCode':200}}}
    put('/etc/hy2-udp443/server.private.json',hy2)
    put('/etc/hy2-udp443/accounts.private.json',{'users':{p:{'counter_user':'California,'+p} for p in c['users']}})
    put('/etc/hy2-udp443/collection-enabled','')
    put('/etc/userm/settings.json',{'host':c['host'],'endpoints':{'xray-xhttp443.service:XHTTP443':{'host':c['host'],'sni':c['tls_name']}}})
    copy('modules/userm/userm.py','/opt/vps-modules/userm/1.0.1/userm.py',0o755)
    copy('modules/usage/collector.py','/root/xray-usage-collect.py',0o700)
    copy('modules/usage/xray_common.py','/root/xray_common.py')
    copy('modules/usage/monthly.py','/root/xray-v1-monthly-v2.py',0o700)
    copy('modules/usage/legacy_reset.py','/opt/la-portal/legacy_reset.py',0o700)
    put('/root/xray-monthly-reset.sh','#!/bin/sh\nexec /usr/bin/python3 /opt/la-portal/legacy_reset.py\n',0o700)
    for p in (ROOT/'modules/usage/v1_core').glob('*.py'):copy(str(p.relative_to(ROOT)),'/opt/la-portal/v1_core/'+p.name)
    for name in ('tick',):copy('modules/usage/'+name+'.py','/opt/la-portal/'+name+'.py',0o700)
    copy('scripts/init-new-v1.py','/opt/la-portal/init-new-v1.py')
    copy('scripts/self_test.py','/opt/la-portal/self_test.py')
    copy('modules/usage/schema.sql','/opt/la-portal/schema.sql')
    mapping={}
    for p in c['users']:
        mapping['California,'+p+'-vless_reality_vision']=p
        mapping['California,'+p]=p
    put('/etc/la-portal/identities.json',mapping)
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    put('/var/lib/xray-usage/current.json',{'period_started':now,'last_updated':now,'baseline_user_bytes':0,'users':{},'last_counters':{},
        'xhttp443_last_counters':{},'hy2_last_counters':{},'collector_generations':{}})
    put('/etc/systemd/system/xray.service',unit('LA Portal REALITY','/etc/v2ray-agent/xray/xray run -confdir /etc/v2ray-agent/xray/conf'),0o644)
    put('/etc/systemd/system/xray-xhttp443.service',unit('LA Portal XHTTP TLS','/usr/local/lib/xray-xhttp443/xray run -config /etc/xray-xhttp443/current/config.json'),0o644)
    put('/etc/systemd/system/hy2-udp443.service',unit('LA Portal HY2','/usr/local/lib/hy2-udp443/hysteria server --config /etc/hy2-udp443/server.private.json --disable-update-check'),0o644)
    def timer(name,command,calendar=None):
        service=unit('LA Portal '+name,command).replace('Type=simple','Type=oneshot').replace('Restart=on-failure\n','')
        if name=='renew': service=service.replace('Type=oneshot','Type=oneshot\nTimeoutStartSec=15min')
        put('/etc/systemd/system/la-'+name+'.service',service,0o644)
        schedule = 'OnCalendar='+calendar if calendar else 'OnBootSec=30s\nOnUnitActiveSec=60s'
        put('/etc/systemd/system/la-'+name+'.timer','[Unit]\nDescription=LA Portal '+name+' timer\n[Timer]\n'+schedule+'\nAccuracySec=1s\nPersistent=true\n[Install]\nWantedBy=timers.target\n',0o644)
    timer('usage','/usr/bin/flock /var/lock/xray-usage-v1.lock /usr/bin/python3 /opt/la-portal/tick.py')
    timer('monthly','/usr/bin/python3 /root/xray-v1-monthly-v2.py',calendar(billing))
    if c.get('certificate_renewal_command'):
        put('/etc/la-portal/renew.json',{'argv':c['certificate_renewal_command'],'cert':c['tls_cert'],'key':c['tls_key'],'tls_name':c['tls_name']})
        copy('scripts/renew.py','/opt/la-portal/renew.py',0o700)
        timer('renew','/usr/bin/python3 /opt/la-portal/renew.py','*-*-* 00,06,12,18:00:00 UTC')
    manifest={'files':files,'monthly_enabled':bool(c.get('enable_monthly')),'mail_enabled':False,'renew_enabled':bool(c.get('certificate_renewal_command'))}
    (d/'manifest.json').write_text(json.dumps(manifest,indent=2));(d/'manifest.json').chmod(0o600)
    return manifest

def command(args, **kw):
    if args[0]=='apt-get': kw.setdefault('env',dict(os.environ,DEBIAN_FRONTEND='noninteractive'))
    return subprocess.run(args,check=True,**kw)

def preflight(managed_acme=False):
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Install only on Linux as root')
    if platform.machine() not in ('x86_64','amd64'):raise ValueError('This release supports amd64 only')
    release=pathlib.Path('/etc/os-release').read_text()
    if 'ID=ubuntu' not in release or not any(v in release for v in ('22.04','24.04')):raise ValueError('Ubuntu22.04/24.04 required')
    for p in ('/etc/v2ray-agent','/etc/xray-xhttp443','/etc/hy2-udp443','/opt/la-portal','/etc/la-portal','/usr/local/sbin/userm','/var/lib/xray-usage-v1/usage.db'):
        if pathlib.Path(p).exists() or pathlib.Path(p).is_symlink():raise ValueError('Fresh-server only; existing path: '+p)
    output=subprocess.check_output(['ss','-H','-lntu'],text=True)
    if managed_acme and re.search(r':80\s',output):
        raise ValueError('ACME standalone requires TCP80 free; do not stop existing web services blindly')
    for line in output.splitlines():
        if re.search(r':(?:8443|443|31305|10085|10086|10087)\s',line):raise ValueError('Required port occupied')

def fetch_binaries(folder):
    result={}
    for name,(url,digest) in ARTIFACTS.items():
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'LA-Portal/0.1'}),timeout=90) as response:
            data=response.read(100_000_001)
        if len(data)>100_000_000 or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Artifact hash mismatch: '+name)
        if name=='xray':
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as z:data=z.read('xray')
        target=folder/name;target.write_bytes(data);target.chmod(0o755);result[name]=target
    return result

def validate_tls(c):
    subprocess.run(['openssl','verify','-CAfile','/etc/ssl/certs/ca-certificates.crt','-untrusted',c['tls_cert'],c['tls_cert']],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['openssl','x509','-in',c['tls_cert'],'-noout','-checkend','86400'],check=True,stdout=subprocess.DEVNULL)
    try:ipaddress.ip_address(c['tls_name']);flag='-checkip'
    except ValueError:flag='-checkhost'
    subprocess.run(['openssl','x509','-in',c['tls_cert'],'-noout',flag,c['tls_name']],check=True,stdout=subprocess.DEVNULL)
    cert=subprocess.check_output(['openssl','x509','-in',c['tls_cert'],'-pubkey','-noout'])
    key=subprocess.check_output(['openssl','pkey','-in',c['tls_key'],'-pubout'])
    if cert!=key:raise ValueError('Certificate/private key mismatch')

def install(c):
    managed = c.get('certificate',{}).get('mode') == 'acme'
    preflight(managed)
    command(['apt-get','update'])
    command(['apt-get','install','-y','ca-certificates','python3','python3-openpyxl','openssl','iproute2','python3-venv','curl'])
    from scripts.certificates import prepare
    prepare(c)
    validate_tls(c)
    folder=pathlib.Path(tempfile.mkdtemp(prefix='la-portal-install-',dir='/root'));folder.chmod(0o700)
    bins=fetch_binaries(folder)
    tree=folder/'stage';manifest=stage(c,tree)
    # Validate exact staged Xray configurations after substituting certificate paths.
    xhttp=json.loads((tree/'etc/xray-xhttp443/current/config.json').read_text())
    tls=xhttp['inbounds'][1]['streamSettings']['tlsSettings']['certificates'][0]
    tls['certificateFile']=c['tls_cert'];tls['keyFile']=c['tls_key']
    check=folder/'xhttp-check.json';check.write_text(json.dumps(xhttp));check.chmod(0o600)
    command([str(bins['xray']),'run','-test','-confdir',str(tree/'etc/v2ray-agent/xray/conf')],stdout=subprocess.DEVNULL)
    command([str(bins['xray']),'run','-test','-config',str(check)],stdout=subprocess.DEVNULL)
    journal=pathlib.Path('/var/lib/la-portal/install.json');journal.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    record={'status':'installing','stage':str(folder),'units':[u for u in UNITS if '/etc/systemd/system/'+u in manifest['files']],'files':manifest['files']}
    journal.write_text(json.dumps(record));journal.chmod(0o600)
    try:
        for target,meta in manifest['files'].items():
            p=pathlib.Path(target)
            if p.exists():raise ValueError('Refusing to overwrite '+target)
            p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            shutil.copyfile(tree/target.lstrip('/'),p);p.chmod(meta['mode'])
        for src,target in ((bins['xray'],'/etc/v2ray-agent/xray/xray'),(bins['xray'],'/usr/local/lib/xray-xhttp443/xray'),(bins['hysteria'],'/usr/local/lib/hy2-udp443/hysteria')):
            p=pathlib.Path(target);p.parent.mkdir(mode=0o755,parents=True,exist_ok=True);shutil.copyfile(src,p);p.chmod(0o755)
        pathlib.Path('/var/lib/xray-usage-v1').mkdir(mode=0o700,parents=True,exist_ok=True)
        pathlib.Path('/var/lib/userm').mkdir(mode=0o700,parents=True,exist_ok=True)
        pathlib.Path('/usr/local/sbin/userm').symlink_to('/opt/vps-modules/userm/1.0.1/userm.py')
        command(['systemctl','daemon-reload'])
        for u in UNITS[:3]:command(['systemctl','enable','--now',u])
        command(['/usr/bin/flock','/var/lock/xray-usage-v1.lock','/usr/bin/python3','/opt/la-portal/tick.py'])
        command(['/usr/bin/python3','/opt/la-portal/self_test.py'])
        command(['systemctl','enable','--now','la-usage.timer'])
        if manifest['renew_enabled']:command(['systemctl','enable','--now','la-renew.timer'])
        # Email and monthly remain off until explicit acceptance via activate.
        record['status']='installed-awaiting-acceptance'
    except BaseException:
        for u in UNITS:subprocess.run(['systemctl','disable','--now',u],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        record['status']='failed-stopped-data-retained'
        raise
    finally:journal.write_text(json.dumps(record));journal.chmod(0o600)
    print('Installed. Verify real clients and usage before activate. Private stage:',folder)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['setup','mail-setup','mail-disable','mail-test','billing-show','billing-set','core-check','core-upgrade','core-rollback','self-test','plan','stage','install','status','activate','rollback'])
    p.add_argument('--config');p.add_argument('--out');p.add_argument('--apply',action='store_true')
    p.add_argument('--clients-verified',action='store_true')
    p.add_argument('--day',type=int)
    p.add_argument('--time',dest='billing_time')
    p.add_argument('--timezone')
    p.add_argument('--quota')
    p.add_argument('--unit',choices=['GB','GiB'])
    p.add_argument('--factor')
    p.add_argument('--component',choices=['xray','hy2'])
    p.add_argument('--version')
    p.add_argument('--backup')
    a=p.parse_args()
    if a.action in ('core-check','core-upgrade','core-rollback'):
        from scripts import core_update
        if a.action=='core-check':
            for component in ([a.component] if a.component else ['xray','hy2']):core_update.status(component)
        else:
            if not a.apply or sys.platform!='linux' or os.geteuid()!=0:p.error('Core changes require Linux root and --apply')
            if a.action=='core-upgrade':
                if not a.component or not a.version:p.error('Provide --component and explicit --version')
                core_update.upgrade(a.component,a.version)
            else:core_update.rollback(a.backup)
    elif a.action in ('billing-show','billing-set'):
        from modules.usage.v1_core.billing import load, normalize
        c=load()
        if a.action=='billing-show':print(json.dumps(c,indent=2))
        else:
            if not a.apply or sys.platform!='linux' or os.geteuid()!=0:p.error('billing-set requires Linux root and --apply')
            updates={k:v for k,v in {'day':a.day,'time':a.billing_time,'timezone':a.timezone,'quota':a.quota,'unit':a.unit,'factor':a.factor}.items() if v is not None}
            if not updates:p.error('Provide at least one billing setting')
            from scripts.billing_update import apply_settings
            apply_settings(normalize(dict(c,**updates)))
    elif a.action=='self-test':
        if not a.apply or sys.platform!='linux' or os.geteuid()!=0:p.error('self-test requires Linux root and --apply')
        command(['/usr/bin/python3','/opt/la-portal/self_test.py'])
    elif a.action=='setup':
        from scripts.setup import configure
        configure(a.config or '/root/private/la-portal.json')
    elif a.action=='mail-setup':
        if not a.apply or sys.platform!='linux' or os.geteuid()!=0:p.error('mail-setup requires Linux root and --apply')
        from scripts.mail_setup import install as install_mail
        import fcntl
        with open('/var/lock/la-portal-mail.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            install_mail(ROOT)
    elif a.action=='mail-disable':
        if not a.apply or sys.platform!='linux' or os.geteuid()!=0:p.error('mail-disable requires Linux root and --apply')
        command(['systemctl','disable','--now','la-weekly.timer'])
        command(['systemctl','stop','la-weekly.service'])
        print('Mail scheduling stopped; settings, credentials and sent markers retained')
    elif a.action=='mail-test':
        if not a.apply or sys.platform!='linux' or os.geteuid()!=0:p.error('mail-test requires Linux root and --apply')
        command(['/usr/bin/python3','/opt/la-portal/mail_test.py'])
    elif a.action in ('plan','stage','install'):
        c=read_config(a.config)
        if a.action=='plan':
            print('New Ubuntu amd64 only; REALITY TCP443 + XHTTP TCP8443 + HY2 UDP443 + userm + V1; no history; no Tunnel.')
            print('Billing policy:',json.dumps(c['billing']))
            print('Email configured:',bool(c.get('mail')),'monthly requested:',bool(c.get('enable_monthly')))
            if c.get('certificate',{}).get('mode')=='acme':print('ACME standalone temporarily listens on TCP80; CA terms accepted; staging then production issuance.')
            print('Managed ACME certificate:', c.get('certificate',{}).get('mode')=='acme')
            print('Certificate renewal configured:',c.get('certificate',{}).get('mode')=='acme' or bool(c.get('certificate_renewal_command')))
        elif a.action=='stage':
            if c.get('certificate',{}).get('mode')=='acme':
                raise ValueError('ACME issuance is an install action; stage only accepts preissued import certificates')
            stage(c,a.out);print('Private staged files generated; no live paths modified')
        else:
            if not a.apply:p.error('install requires --apply')
            import fcntl
            with open('/var/lock/la-portal-install.lock','a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
                install(c)
    elif a.action=='status':
        subprocess.run(['systemctl','is-active',*UNITS])
        if pathlib.Path('/var/lib/xray-usage-v1/usage.db').exists():
            with sqlite3.connect('file:/var/lib/xray-usage-v1/usage.db?mode=ro',uri=True) as db:
                print('Integrity:',db.execute('PRAGMA integrity_check').fetchone()[0])
                print('FK errors:',len(db.execute('PRAGMA foreign_key_check').fetchall()))
                print('Last sample:',db.execute("SELECT value FROM v1_live_meta WHERE key='last_state_at'").fetchone())
    else:
        if not a.apply or sys.platform!='linux' or os.geteuid()!=0:p.error('Requires Linux root and --apply')
        j=pathlib.Path('/var/lib/la-portal/install.json');record=json.loads(j.read_text())
        if a.action=='rollback':
            if pathlib.Path('/etc/systemd/system/la-weekly.timer').exists():
                command(['systemctl','disable','--now','la-weekly.timer'])
                command(['systemctl','stop','la-weekly.service'])
            for u in record['units']:subprocess.run(['systemctl','disable','--now',u],check=True)
            record['status']='stopped-data-retained';j.write_text(json.dumps(record))
            print('Stopped installed units. Configurations, credentials and data retained. Packages not removed.')
        else:
            if not a.clients_verified:p.error('activate requires --clients-verified after real client checks')
            if record['status']!='installed-awaiting-acceptance':raise ValueError('Unexpected installation status')
            manifest=json.loads((pathlib.Path(record['stage'])/'stage/manifest.json').read_text())
            # Admission requires an actual successful collection, not only active processes.
            for unit in UNITS[:3]:command(['systemctl','is-active','--quiet',unit])
            command(['systemctl','start','la-usage.service'])
            if manifest['monthly_enabled']:
                command(['/usr/bin/python3','/root/xray-v1-monthly-v2.py','--check'])
                command(['systemctl','enable','--now','la-monthly.timer'])
            record['status']='accepted';j.write_text(json.dumps(record))
            print('Accepted; requested monthly timer enabled. Mail is installed independently. Natural cycle still unverified.')

if __name__=='__main__':main()

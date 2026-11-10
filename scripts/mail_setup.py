"""Optional mail installation after the proxy and V1 installation is working."""
import datetime
import getpass
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

from scripts.setup import prompt

def atomic(path, text, mode=0o600):
    path = pathlib.Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.mail-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def smtp_config(c):
    for key in ('host', 'user', 'password', 'from', 'to'):
        if not c.get(key) or any(ch in c[key] for ch in '\r\n'):
            raise ValueError('Invalid SMTP field: '+key)
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    return ('defaults\nauth on\ntls on\ntls_starttls on\n'
            'tls_trust_file /etc/ssl/certs/ca-certificates.crt\naccount portal\n' +
            '\n'.join(key+' '+quote(c[key]) for key in ('host','port','user','password','from'))+'\n')

def install(root):
    if not pathlib.Path('/var/lib/la-portal/install.json').is_file():
        raise ValueError('Install and verify the base stack first')
    if not pathlib.Path('/var/lib/xray-usage-v1/usage.db').is_file():
        raise ValueError('V1 baseline must exist before mail installation')
    sender = prompt('Gmail sending address')
    c = {'host':'smtp.gmail.com','port':587,'user':sender,'from':sender,
         'password':getpass.getpass('Gmail application password (hidden): '),
         'to':prompt('Weekly report recipient',sender)}
    text = smtp_config(c)
    subprocess.run(['apt-get','install','-y','msmtp'],check=True,env=dict(os.environ,DEBIAN_FRONTEND='noninteractive'))
    units = pathlib.Path('/etc/systemd/system')
    targets = [pathlib.Path('/etc/la-portal/msmtprc'),pathlib.Path('/etc/la-portal/mail.json'),
               units/'la-weekly.service',units/'la-weekly.timer']
    backup = pathlib.Path('/var/lib/xray-v1-mail')/('config-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700,parents=True)
    for index,path in enumerate(targets):
        if path.exists():
            shutil.copy2(path,backup/str(index))
            (backup/str(index)).chmod(0o600)
    subprocess.run(['systemctl','stop','la-weekly.timer'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl','stop','la-weekly.service'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    # A failed reconfiguration must leave delivery disabled, not keep using stale credentials.
    subprocess.run(['systemctl','disable','la-weekly.timer'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    marker = pathlib.Path('/var/lib/xray-v1-mail/smtp-tested')
    if marker.exists():
        marker.unlink()
    atomic(targets[0],text)
    atomic(targets[1],json.dumps({'from':c['from'],'to':c['to']}))
    for source,destination in (('scripts/mail_test.py','mail_test.py'),('modules/usage/weekly.py','weekly.py')):
        atomic(pathlib.Path('/opt/la-portal')/destination,(root/source).read_text(),0o700)
    atomic(targets[2],'[Unit]\nDescription=LA Portal independent weekly mail\nAfter=network-online.target\n[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/bin/python3 /opt/la-portal/weekly.py\nTimeoutStartSec=10min\n',0o644)
    atomic(targets[3],'[Unit]\nDescription=LA Portal weekly mail timer\n[Timer]\nOnCalendar=Fri *-*-* 01:10:00 UTC\nPersistent=true\n[Install]\nWantedBy=timers.target\n',0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('Sending one SMTP configuration test (not a usage report).')
    subprocess.run(['/usr/bin/python3','/opt/la-portal/mail_test.py'],check=True)
    subprocess.run(['systemctl','enable','--now','la-weekly.timer'],check=True)
    print('Mail installed; Friday 01:10 UTC weekly timer enabled. Confirm test email arrival.')
    print('Previous mail settings, if any, backed up privately:',backup)

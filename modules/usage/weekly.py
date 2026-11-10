#!/usr/bin/env python3
import fcntl
import json
import pathlib
import subprocess
from datetime import datetime, timezone, timedelta
from email import policy
from email.parser import BytesParser
B = pathlib.Path('/opt/la-portal')
state = pathlib.Path('/var/lib/xray-v1-mail')
state.mkdir(mode=0o700, exist_ok=True)
with (state/'weekly.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Prevent atomic DB replacement while report reads and hashes the file.
    with open('/var/lock/xray-usage-v1.lock','a') as vlock:
        fcntl.flock(vlock, fcntl.LOCK_EX)
        subprocess.run(['/usr/bin/python3', str(B/'v1_core/generate_reports.py')], check=True)
    config = json.loads(pathlib.Path('/etc/la-portal/mail.json').read_text())
    now = datetime.now(timezone(timedelta(hours=8)))
    end = now.replace(hour=9,minute=0,second=0,microsecond=0)
    end -= timedelta(days=(end.weekday()-4)%7)
    if end > now: end -= timedelta(days=7)
    key = end.strftime('%Y-%m-%d')
    marker = state/(key+'.sent')
    if marker.exists(): raise SystemExit('Already submitted')
    msg = BytesParser(policy=policy.default).parsebytes((B/'output-friday-v1/weekly_email_preview.eml').read_bytes())
    for header in ('Subject','To','From'):
        if header in msg: del msg[header]
    msg['Subject'] = 'Xray V1 Weekly Usage Report - '+key
    msg['To'] = config['to']
    msg['From'] = config['from']
    body = msg.get_body(preferencelist=('plain',))
    body.set_content('Missing historical hours are display zeros, not measured zero traffic.\n\n'+body.get_content().replace('PREVIEW ONLY - NOT SENT','PRODUCTION REPORT'))
    subprocess.run(['msmtp','-C','/etc/la-portal/msmtprc','-a','portal',config['to']], input=msg.as_bytes(), check=True)
    marker.write_text(datetime.now(timezone.utc).isoformat())
    marker.chmod(0o600)
    print('SMTP_ACCEPTED',key)

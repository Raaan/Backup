"""Send an explicit configuration test using the installed SMTP account."""
import datetime
import json
import pathlib
import subprocess
from email.message import EmailMessage

def send(config_path='/etc/la-portal/mail.json', state_dir='/var/lib/xray-v1-mail', runner=subprocess.run):
    c = json.loads(pathlib.Path(config_path).read_text())
    msg = EmailMessage()
    msg['From'] = c['from']
    msg['To'] = c['to']
    msg['Subject'] = 'LA Portal SMTP configuration test'
    msg.set_content('LA Portal SMTP test. This is not a traffic report.\nUTC: ' +
                    datetime.datetime.now(datetime.timezone.utc).isoformat())
    result = runner(['msmtp', '-C', '/etc/la-portal/msmtprc', '-a', 'portal', c['to']],
                            input=msg.as_bytes(), capture_output=True)
    if result.returncode:
        raise RuntimeError('SMTP test failed; check local SMTP settings; credentials were not printed')
    marker = pathlib.Path(state_dir)/'smtp-tested'
    marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker.write_text(datetime.datetime.now(datetime.timezone.utc).isoformat())
    marker.chmod(0o600)
    print('SMTP accepted test message. Confirm arrival in recipient inbox.')

if __name__ == '__main__':
    send()

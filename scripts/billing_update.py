"""Apply billing settings without resetting counters, periods, or proxy services."""
import datetime
import json
import pathlib
import shutil
import subprocess
from modules.usage.v1_core.billing import CONFIG, normalize, load, calendar
from scripts.mail_setup import atomic

INSTALL_RECORD = pathlib.Path('/var/lib/la-portal/install.json')
LOCK = pathlib.Path('/var/lock/xray-usage-v1.lock')
DROPIN = pathlib.Path('/etc/systemd/system/la-monthly.timer.d/billing.conf')
BACKUPS = pathlib.Path('/var/lib/la-portal')

def apply_settings(new):
    import fcntl
    new = normalize(new)
    if not INSTALL_RECORD.is_file():
        raise ValueError('Install the base stack first')
    if not CONFIG.is_file():
        raise ValueError('Billing-capable base installation required; do not apply to an older deployment')
    old = load(CONFIG)
    changed = calendar(old) != calendar(new)
    timer = 'la-monthly.timer'
    dropin = DROPIN
    was_active = subprocess.run(['systemctl','is-active','--quiet',timer]).returncode == 0
    with open(LOCK,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        if changed:
            subprocess.run(['systemctl','stop',timer],check=True)
        try:
            state = subprocess.check_output(['systemctl','show','la-monthly.service','-p','ActiveState','--value'],text=True).strip()
            if state not in ('inactive','failed'):
                raise ValueError('Monthly job is running; wait before changing billing')
            backup = BACKUPS/('billing-backup-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            backup.mkdir(mode=0o700)
            for source,name in ((CONFIG,'billing.json'),(dropin,'billing.conf')):
                if source.exists():
                    shutil.copy2(source,backup/name)
                    (backup/name).chmod(0o600)
            atomic(CONFIG,json.dumps(new,indent=2)+'\n')
            try:
                if changed:
                    atomic(dropin,'[Timer]\nOnCalendar=\nOnCalendar='+calendar(new)+'\n',0o644)
                    subprocess.run(['systemctl','daemon-reload'],check=True)
                    # Clear only the stopped timer's persistent timestamp: do not
                    # immediately replay a past date from the newly chosen schedule.
                    subprocess.run(['systemctl','clean','--what=state',timer],check=True)
            except BaseException:
                atomic(CONFIG,json.dumps(old,indent=2)+'\n')
                saved = backup/'billing.conf'
                if saved.exists():atomic(dropin,saved.read_text(),0o644)
                elif dropin.exists():dropin.unlink()
                subprocess.run(['systemctl','daemon-reload'],check=False)
                raise
        finally:
            if changed and was_active:
                subprocess.run(['systemctl','start',timer],check=True)
    print(json.dumps(new,indent=2))
    print('Policy updated. Current period and traffic retained; no proxy restart or quota enforcement.')
    print('Backup:',backup)

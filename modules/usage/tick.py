#!/usr/bin/env python3
"""Called with the V1 lock already held by systemd or monthly.py."""
import fcntl
import json
import pathlib
import shutil
import subprocess
import sys
B = pathlib.Path('/opt/la-portal')
DB = pathlib.Path('/var/lib/xray-usage-v1/usage.db')
STATE = pathlib.Path('/var/lib/xray-usage/current.json')
with open('/var/lock/xray-usage.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    subprocess.run(['/usr/bin/python3', '/root/xray-usage-collect.py'], check=True)
    if not DB.exists():
        # Register zero counters even when no clients have connected yet.
        sample = json.loads(STATE.read_text())
        identities = json.loads(pathlib.Path('/etc/la-portal/identities.json').read_text())
        for registry in ('last_counters', 'xhttp443_last_counters', 'hy2_last_counters'):
            for raw in identities:
                is_xray = raw.endswith('-vless_reality_vision')
                if is_xray != (registry != 'hy2_last_counters'):
                    continue
                for direction in ('uplink', 'downlink'):
                    sample.setdefault(registry, {}).setdefault('user>>>'+raw+'>>>traffic>>>'+direction, 0)
        STATE.write_text(json.dumps(sample), encoding='utf-8')
        STATE.chmod(0o600)
        out = B / 'bootstrap-state'
        subprocess.run(['/usr/bin/python3', str(B/'init-new-v1.py'), '--schema', str(B/'schema.sql'),
                        '--sample', str(STATE), '--identities', '/etc/la-portal/identities.json',
                        '--out', str(out)], check=True)
        shutil.copy2(out/'usage.db', DB)
        shutil.copy2(out/'current.json', STATE)
        print('NEW_ZERO_HISTORY_BASELINE_CREATED')
        sys.exit(0)
sys.path.insert(0, str(B/'v1_core'))
sys.path.insert(0, '/root')
from live_ingest import run
from lifecycle import read_inventory
run(inventory=read_inventory())

#!/usr/bin/env python3
"""Legacy final capture + Main reset. Invoked only by monthly coordinator."""
import fcntl
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
sys.path.insert(0, '/root')
from importlib.machinery import SourceFileLoader
collector = SourceFileLoader('legacy_collector', '/root/xray-usage-collect.py').load_module()
STATE = pathlib.Path('/var/lib/xray-usage/current.json')
HISTORY = STATE.parent/'history'
with open('/var/lock/xray-usage.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if collector.collect() != 0:
        raise RuntimeError('Incomplete final collection; reset aborted')
    previous = json.loads(STATE.read_text())
    HISTORY.mkdir(mode=0o700, exist_ok=True)
    archive = HISTORY / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    archive.write_text(json.dumps(previous), encoding='utf-8')
    archive.chmod(0o600)
    subprocess.run(['/etc/v2ray-agent/xray/xray', 'api', 'statsquery', '--server=127.0.0.1:10085', '-reset'],
                   check=True, stdout=subprocess.DEVNULL)
    now = datetime.now(timezone.utc).isoformat()
    state = {'period_started':now, 'last_updated':now, 'baseline_user_bytes':0, 'users':{}, 'last_counters':{}}
    for key in ('xhttp443_last_counters', 'hy2_last_counters', 'collector_generations'):
        state[key] = previous[key]
    collector.save_state(state)

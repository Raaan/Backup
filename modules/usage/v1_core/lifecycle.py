"""Account inventory and next-period retirement. Never deletes usage history."""
import json
from pathlib import Path

from fusion import resolve
from shadow_reconcile import SOURCES


def read_inventory(root=Path('/')):
    root = Path(root)
    result = []
    main = root/'etc/v2ray-agent/xray/conf'
    main_files = sorted(main.glob('*.json'))
    if not main_files:
        raise ValueError('Xray inventory unavailable; account retirement paused')
    configs = ((SOURCES[0][0], main_files),
               (SOURCES[1][0], [root/'etc/xray-xhttp443/current/config.json']))
    for source, paths in configs:
        for path in paths:
            config = json.loads(path.read_text())
            for inbound in config['inbounds']:
                if inbound.get('protocol') != 'vless':
                    continue
                for client in inbound['settings']['clients']:
                    raw = client.get('email')
                    if not isinstance(raw, str) or not raw.strip():
                        raise ValueError('VLESS user has no accounting identity')
                    result.append((source, raw))
    hy2 = json.loads((root/'etc/hy2-udp443/server.private.json').read_text())
    if hy2['auth']['type'] != 'userpass':
        raise ValueError('Unsupported HY2 inventory authentication')
    users = json.loads((root/'etc/hy2-udp443/accounts.private.json').read_text())['users']
    mapping = {name.casefold(): data['counter_user'] for name, data in users.items()}
    if len(mapping) != len(users):
        raise ValueError('Ambiguous HY2 inventory names')
    for name in hy2['auth']['userpass']:
        result.append((SOURCES[2][0], mapping.get(name.casefold(), name)))
    return result


def synchronize(db, period, inventory):
    """Caller owns the staged transaction. Absence in one protocol is insufficient."""
    db.execute('''CREATE TABLE IF NOT EXISTS v1_account_lifecycle (
        account_id TEXT PRIMARY KEY REFERENCES accounts(account_id),
        absent_since_period TEXT REFERENCES billing_periods(period_id))''')
    present = set()
    for source, raw in inventory:
        account, status = resolve(db, source, raw, ['*'])
        if status != 'mapped':
            raise ValueError('Configured user identity cannot be fused')
        present.add(account)
    for account, in db.execute('SELECT account_id FROM accounts').fetchall():
        if account in present:
            db.execute("UPDATE accounts SET status='active' WHERE account_id=?", (account,))
            db.execute('''INSERT INTO v1_account_lifecycle VALUES (?,NULL)
                ON CONFLICT(account_id) DO UPDATE SET absent_since_period=NULL''', (account,))
        else:
            db.execute('''INSERT INTO v1_account_lifecycle VALUES (?,?)
                ON CONFLICT(account_id) DO UPDATE SET
                absent_since_period=COALESCE(absent_since_period,excluded.absent_since_period)''',
                       (account, period))


def retire_absent(db, new_period):
    """Called inside monthly transition; retain aliases, counters and historical rows."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v1_account_lifecycle'").fetchone():
        return
    db.execute('''UPDATE accounts SET status='inactive' WHERE account_id IN (
        SELECT account_id FROM v1_account_lifecycle
        WHERE absent_since_period IS NOT NULL AND absent_since_period<>?)''', (new_period,))

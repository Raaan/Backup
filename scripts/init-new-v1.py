#!/usr/bin/env python3
"""Create a NEW zero-history V1 baseline in an isolated directory only.
No network, services, scheduling or production writes. Review before installing.
"""
import argparse
import datetime
import json
import pathlib
import sqlite3

SOURCES = (
    ('fair-light-1/xray-main', 'main', 'last_counters', 'xray', 'xray.service', '127.0.0.1:10085'),
    ('fair-light-1/xhttp443', 'xhttp443', 'xhttp443_last_counters', 'xray', 'xray-xhttp443.service', '127.0.0.1:10086'),
    ('fair-light-1/hy2-udp443', 'hy2', 'hy2_last_counters', 'hysteria2', 'hy2-udp443.service', '127.0.0.1:10087'),
)

def build(args):
    out = pathlib.Path(args.out).resolve()
    # This program cannot deploy directly into the live data directories.
    for forbidden in (pathlib.Path('/var/lib'), pathlib.Path('/etc'), pathlib.Path('/usr')):
        if out == forbidden or forbidden in out.parents:
            raise ValueError('Output must be an isolated staging directory')
    if out.exists():
        raise ValueError('Output already exists; refusing overwrite')
    mapping = json.loads(pathlib.Path(args.identities).read_text(encoding='utf-8-sig'))
    if not mapping or any('__' in str(k) + str(v) for k, v in mapping.items()):
        raise ValueError('Fill identity mapping before initialization')
    state = json.loads(pathlib.Path(args.sample).read_text(encoding='utf-8-sig'))
    stamp = state['last_updated']
    dt = datetime.datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        raise ValueError('Sample timestamp requires timezone')
    # Preserve timestamp spelling: live_ingest compares period strings exactly.
    state['period_started'] = stamp
    state['baseline_user_bytes'] = 0
    state['users'] = {}
    schema = pathlib.Path(args.schema).read_text(encoding='utf-8-sig')
    parsed = []
    for sid, generation, registry, adapter, unit, endpoint in SOURCES:
        gen = state['collector_generations'][generation]
        if not isinstance(gen, str) or not gen:
            raise ValueError('Missing generation for ' + sid)
        for key, value in state.get(registry, {}).items():
            fields = key.split('>>>')
            if (len(fields) != 4 or fields[0] != 'user' or fields[2] != 'traffic'
                    or fields[3] not in ('uplink', 'downlink')
                    or type(value) is not int or value < 0):
                raise ValueError('Invalid sample counter')
            raw = fields[1]
            if raw not in mapping:
                raise ValueError('Explicit mapping required: ' + raw)
            parsed.append((sid, raw, fields[3], gen, value, stamp))
    people = {}
    for raw, person in mapping.items():
        if not isinstance(raw, str) or not raw or not isinstance(person, str) or not person:
            raise ValueError('Invalid identity mapping')
        # Account IDs follow production fusion convention.
        aid = 'acc_' + person.casefold()
        if aid in people and people[aid] != person:
            raise ValueError('Canonical names collide under case folding')
        people[aid] = person
    out.mkdir(mode=0o700, parents=True)
    dbpath = out / 'usage.db'
    with sqlite3.connect(dbpath) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(schema)
        with db:
            db.execute('INSERT INTO billing_periods VALUES (?,?,NULL,0)', (stamp, stamp))
            db.execute('INSERT INTO v1_live_meta VALUES (?,?)', ('last_state_at', stamp))
            for aid, person in people.items():
                db.execute('INSERT INTO accounts VALUES (?,?,?)', (aid, person, 'active'))
                db.execute('INSERT INTO account_aliases VALUES (?,?,?)', (person, aid, 'canonical'))
            for raw, person in mapping.items():
                aid = 'acc_' + person.casefold()
                row = db.execute('SELECT account_id FROM account_aliases WHERE alias=?', (raw,)).fetchone()
                if row and row[0] != aid:
                    raise ValueError('Alias conflict')
                if not row:
                    db.execute('INSERT INTO account_aliases VALUES (?,?,?)', (raw, aid, 'explicit'))
            for sid, generation, registry, adapter, unit, endpoint in SOURCES:
                db.execute('INSERT INTO sources VALUES (?,?,?,?,?,1)',
                           (sid, sid.split('/')[0], adapter, unit, endpoint))
            pairs = {(row[0], row[1]) for row in parsed}
            for sid, raw in sorted(pairs):
                db.execute('INSERT INTO source_accounts VALUES (?,?,?,?)',
                           (sid, raw, 'acc_' + mapping[raw].casefold(), 'mapped'))
            db.executemany('INSERT INTO source_counter_state VALUES (?,?,?,?,?,?)', parsed)
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Integrity failure')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Foreign key failure')
        total = db.execute('SELECT COALESCE(SUM(total_bytes),0) FROM v1_account_period').fetchone()[0]
        if total != 0:
            raise ValueError('New baseline must contain zero usage')
    dbpath.chmod(0o600)
    target = out / 'current.json'
    target.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    target.chmod(0o600)
    print('STAGED_ZERO_HISTORY', out)
    print('No production paths, services or scheduling modified.')

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--schema', required=True)
    p.add_argument('--sample', required=True)
    p.add_argument('--identities', required=True)
    p.add_argument('--out', required=True)
    build(p.parse_args())

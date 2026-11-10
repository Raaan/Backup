"""Shared billing policy. Reporting only; never enforces a proxy traffic cap."""
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONFIG = Path('/etc/la-portal/billing.json')
DEFAULT = {'day':8, 'time':'00:10', 'timezone':'UTC',
           'quota':'1000', 'unit':'GiB', 'factor':'2'}

def normalize(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):
        raise ValueError('Invalid billing fields')
    c = dict(DEFAULT, **value)
    if type(c['day']) is not int or not 1 <= c['day'] <= 28:
        raise ValueError('Billing day must be 1..28 so every month contains it')
    if not isinstance(c['time'],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',c['time']):
        raise ValueError('Billing time must be HH:MM')
    if not isinstance(c['timezone'],str) or not re.fullmatch(r'[A-Za-z0-9_+./-]+',c['timezone']):
        raise ValueError('Invalid billing timezone')
    try:
        ZoneInfo(c['timezone'])
    except ZoneInfoNotFoundError:
        raise ValueError('Unknown timezone; use an installed IANA name such as UTC or Asia/Hong_Kong, not +8') from None
    if c['unit'] not in ('GB','GiB'):
        raise ValueError('Quota unit must be GB or GiB')
    for key in ('quota','factor'):
        if isinstance(c[key],bool):
            raise ValueError('Invalid '+key)
        try:
            number = Decimal(str(c[key]))
        except InvalidOperation:
            raise ValueError('Invalid '+key) from None
        if not number.is_finite() or number <= 0 or number > Decimal('1000000000'):
            raise ValueError(key+' must be a positive finite value <= 1000000000')
        c[key] = str(number)
    if quota_bytes(c) < 1:
        raise ValueError('Quota must be at least one byte')
    return c

def load(path=CONFIG):
    path = Path(path)
    return normalize(json.loads(path.read_text()) if path.exists() else {})

def quota_bytes(c):
    return int(Decimal(c['quota']) * (1000**3 if c['unit']=='GB' else 1024**3))

def estimated_bytes(c,total):
    return int(Decimal(total)*Decimal(c['factor']))

def calendar(c):
    c = normalize(c)
    return f"*-*-{c['day']:02d} {c['time']}:00 {c['timezone']}"

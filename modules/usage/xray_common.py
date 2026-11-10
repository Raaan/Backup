import re

def normalize_user(name):
    clean = re.sub(r'-vless_reality_(?:grpc|vision)$', '', name.strip(), flags=re.I)
    person = clean.replace('，', ',').rsplit(',', 1)[-1].strip()
    if not person:
        raise ValueError('Empty account name')
    return person

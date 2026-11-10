from xray_common import normalize_user
SOURCES = (
    ('fair-light-1/xray-main', 'main', 'last_counters', ['*']),
    ('fair-light-1/xhttp443', 'xhttp443', 'xhttp443_last_counters', ['*']),
    ('fair-light-1/hy2-udp443', 'hy2', 'hy2_last_counters', ['*']),
)

def canonical(raw):
    return normalize_user(raw)

def source_counters(state, registry):
    result = []
    for key, value in state.get(registry, {}).items():
        parts = key.split('>>>')
        if (len(parts) != 4 or parts[0] != 'user' or parts[2] != 'traffic'
                or parts[3] not in ('uplink', 'downlink')
                or type(value) is not int or value < 0):
            raise ValueError('Invalid source counter')
        result.append((parts[1], parts[3], value))
    return result

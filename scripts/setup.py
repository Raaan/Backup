"""Interactive configuration with public-IP discovery; no service changes."""
import ipaddress
import json
import os
import pathlib
import urllib.error
import urllib.request

def prompt(label, default=''):
    value = input(label + (' ['+default+']' if default else '') + ': ').strip()
    return value or default

def detect_public_ipv4():
    addresses = set()
    # Ignore shell proxy settings: discover this VPS's direct egress address.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for url in ('https://api4.ipify.org', 'https://checkip.amazonaws.com'):
        try:
            with opener.open(url, timeout=5) as response:
                address = ipaddress.ip_address(response.read(128).decode('ascii').strip())
            if address.version == 4 and address.is_global:
                addresses.add(str(address))
        except (OSError, ValueError, UnicodeError, urllib.error.URLError):
            continue
    if len(addresses) != 1:
        return None
    return addresses.pop()

def billing_prompt():
    from modules.usage.v1_core.billing import normalize
    policy = normalize({'timezone': 'Asia/Hong_Kong'})
    fields = (
        ('day', 'Monthly settlement day (1-28)'),
        ('time', 'Settlement time HH:MM'),
        ('timezone', 'Settlement timezone (IANA name, e.g. Asia/Hong_Kong; not +8)'),
        ('quota', 'Monthly reporting quota'),
        ('unit', 'Quota unit GB or GiB'),
        ('factor', 'Estimated VPS bandwidth multiplier'),
    )
    for key, label in fields:
        while True:
            value = prompt(label, str(policy[key]))
            try:
                policy = normalize(dict(policy, **{key: int(value) if key == 'day' else value}))
            except (ValueError, KeyError) as error:
                print('Invalid input: ' + str(error) + '. Please enter this field again.')
                continue
            break
    return policy

def configure(destination):
    path = pathlib.Path(destination).expanduser().resolve()
    if path.exists():
        raise ValueError('Configuration exists; refusing overwrite: '+str(path))
    print('New-server ports: REALITY TCP443; XHTTP/TLS TCP8443; HY2 UDP443.')
    print('The exported client links use these ports. TCP80 is for ACME validation.')
    host = detect_public_ipv4()
    if host:
        print('Detected public IPv4 (connection address and TLS certificate identity):', host)
    else:
        print('Public IPv4 discovery failed or providers disagreed; enter the reachable address manually.')
        host = prompt('New VPS public IP or domain').strip('[]')
    email = prompt('ACME contact email (not a mail sending account)')
    c = {'server_id': prompt('Unique server ID', 'california-01'), 'host': host,
         'tls_name': host,
         'reality_sni': prompt('Reachable REALITY TLS target hostname'),
         'users': [v.strip() for v in prompt('Canonical user names, comma separated', 'Owner').split(',')],
         'enable_monthly': True}
    c['billing'] = billing_prompt()
    c['reality_target'] = c['reality_sni'] + ':443'
    print('Certificate issuance needs public TCP80 reachability. No firewall changes are made.')
    print('CA subscriber terms: https://letsencrypt.org/repository/')
    if prompt('Accept CA terms? Type YES') != 'YES':
        raise ValueError('CA terms not accepted; configuration not written')
    c['certificate'] = {'mode': 'acme', 'email': email, 'agree_tos': True}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Exclusive creation; no credential values are printed.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(json.dumps(c, ensure_ascii=False, indent=2)+'\n')
    path.chmod(0o600)
    print('Private configuration saved:', path)
    return path

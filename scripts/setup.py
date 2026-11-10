"""Interactive local configuration; no service changes or network operations."""
import json
import os
import pathlib

def prompt(label, default=''):
    value = input(label + (' ['+default+']' if default else '') + ': ').strip()
    return value or default

def configure(destination):
    path = pathlib.Path(destination).expanduser().resolve()
    if path.exists():
        raise ValueError('Configuration exists; refusing overwrite: '+str(path))
    print('New-server ports: REALITY TCP443; XHTTP/TLS TCP8443; HY2 UDP443.')
    print('The exported client links use these ports. TCP80 is for ACME validation.')
    host = prompt('New VPS public IP or domain').strip('[]')
    email = prompt('ACME contact email (not a mail sending account)')
    c = {'server_id': prompt('Unique server ID', 'california-01'), 'host': host,
         'tls_name': prompt('TLS certificate identity', host),
         'reality_sni': prompt('Reachable REALITY TLS target hostname'),
         'users': [v.strip() for v in prompt('Canonical user names, comma separated', 'Owner').split(',')],
         'enable_monthly': True}
    from modules.usage.v1_core.billing import normalize
    c['billing'] = normalize({'day':int(prompt('Monthly settlement day (1-28)', '8')),
        'time':prompt('Settlement time HH:MM', '00:10'),
        'timezone':prompt('Settlement timezone', 'UTC'),
        'quota':prompt('Monthly reporting quota', '1000'),
        'unit':prompt('Quota unit GB or GiB', 'GiB'),
        'factor':prompt('Estimated VPS bandwidth multiplier', '2')})
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

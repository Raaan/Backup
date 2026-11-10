"""Managed Let's Encrypt issuance for a fresh server. Never used by plan/setup."""
import ipaddress
import pathlib
import subprocess

BASE = pathlib.Path('/var/lib/la-portal/acme')
CERTBOT = pathlib.Path('/opt/la-certbot/bin/certbot')

def paths(environment='production'):
    base = BASE / environment
    return ['--config-dir', str(base/'config'), '--work-dir', str(base/'work'),
            '--logs-dir', str(base/'logs')]

def issue_argv(c, environment='production'):
    spec = c['certificate']
    if spec.get('agree_tos') is not True:
        raise ValueError('Explicit acceptance of CA subscriber terms is required')
    args = [str(CERTBOT), 'certonly', '--standalone', '--non-interactive', '--agree-tos',
            '--email', spec['email'], '--cert-name', 'la-portal', '--key-type', 'ecdsa',
            '--preferred-challenges', 'http', *paths(environment)]
    try:
        ipaddress.ip_address(c['tls_name'])
        args += ['--ip-address', c['tls_name'], '--preferred-profile', 'shortlived']
    except ValueError:
        args += ['-d', c['tls_name']]
    if environment == 'staging':
        args += ['--staging']
    else:
        args += ['--server', 'https://acme-v02.api.letsencrypt.org/directory']
    return args

def renewal_argv():
    return [str(CERTBOT), 'renew', '--cert-name', 'la-portal', '--non-interactive',
            '--no-random-sleep-on-renew', *paths()]

def prepare(c):
    if c.get('certificate', {}).get('mode') != 'acme':
        return
    if c['certificate'].get('agree_tos') is not True:
        raise ValueError('Accept CA terms in setup before issuance')
    # Isolated Python environment: do not replace Ubuntu's Python packages.
    subprocess.run(['/usr/bin/python3', '-m', 'venv', '/opt/la-certbot'], check=True)
    subprocess.run(['/opt/la-certbot/bin/pip', 'install', 'certbot==5.4.0'], check=True)
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Prove HTTP-01 reachability against staging before using production limits.
    subprocess.run(issue_argv(c, 'staging'), check=True)
    subprocess.run(issue_argv(c), check=True)
    live = BASE/'production/config/live/la-portal'
    c['tls_cert'] = str(live/'fullchain.pem')
    c['tls_key'] = str(live/'privkey.pem')
    c['certificate_renewal_command'] = renewal_argv()

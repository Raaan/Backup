"""Explicit Xray/HY2 binary updates for LA Portal installations only."""
import contextlib
import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile

STATE = pathlib.Path('/var/lib/la-portal/core-upgrades')
CORES = {
    'xray': {'repo':'XTLS/Xray-core', 'asset':'Xray-linux-64.zip',
             'files':['/etc/v2ray-agent/xray/xray','/usr/local/lib/xray-xhttp443/xray'],
             'units':['xray.service','xray-xhttp443.service']},
    'hy2': {'repo':'HyNetworks/hysteria', 'asset':'hysteria-linux-amd64',
            'files':['/usr/local/lib/hy2-udp443/hysteria'], 'units':['hy2-udp443.service']},
}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def release(component, version=None):
    spec = CORES[component]
    if version:
        number = version.removeprefix('app/').removeprefix('v')
        if not re.fullmatch(r'\d+\.\d+\.\d+',number):
            raise ValueError('Specify an explicit stable version, such as 26.10.31 or 2.13.0')
        tag = ('v' if component=='xray' else 'app/v')+number
        endpoint = 'tags/'+tag
    else:
        endpoint = 'latest'
    url = 'https://api.github.com/repos/'+spec['repo']+'/releases/'+endpoint
    request = urllib.request.Request(url,headers={'User-Agent':'LA-Portal-core-update'})
    with urllib.request.urlopen(request,timeout=30) as response:
        data = json.load(response)
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('Only stable releases supported')
    asset = next((a for a in data['assets'] if a['name']==spec['asset']),None)
    if not asset or not re.fullmatch(r'sha256:[0-9a-f]{64}',asset.get('digest') or ''):
        raise ValueError('Official SHA-256 asset digest unavailable; refusing update')
    expected = 'https://github.com/'+spec['repo']+'/releases/download/'
    if not asset['browser_download_url'].startswith(expected):
        raise ValueError('Unexpected release asset URL')
    return {'component':component,'version':data['tag_name'],
            'url':asset['browser_download_url'],'sha256':asset['digest'].split(':')[1]}

def payload(info, data):
    if len(data)>100_000_000 or sha(data)!=info['sha256']:
        raise ValueError('Core download SHA-256 mismatch')
    if info['component']=='xray':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member=archive.getinfo('xray')
            if member.file_size>100_000_000:
                raise ValueError('Unexpected binary size')
            data=archive.read(member)
    return data

def atomic(path, data, mode=0o755):
    path=pathlib.Path(path)
    fd,temporary=tempfile.mkstemp(prefix='.core-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),mode)
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)

def run(args):
    # Keep tool output private: config checks may include identity diagnostics.
    result=subprocess.run(args,capture_output=True,text=True,timeout=90)
    if result.returncode:
        raise RuntimeError('Core operation failed: '+pathlib.Path(args[0]).name)
    return result.stdout

def status(component):
    for path in CORES[component]['files']:
        file=pathlib.Path(path)
        print(path)
        print(run([path,'version']).strip() if file.is_file() else 'not installed')
    print('Official release:',json.dumps(release(component),indent=2))

def require_install():
    if not pathlib.Path('/var/lib/la-portal/install.json').is_file():
        raise ValueError('LA Portal base installation required')
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    for journal in STATE.glob('*/journal.json'):
        if json.loads(journal.read_text()).get('status') in ('applying','rollback-needs-review'):
            raise ValueError('Unfinished core change; inspect '+str(journal))

@contextlib.contextmanager
def locks():
    import fcntl
    with contextlib.ExitStack() as stack:
        # V1 before control/legacy; no other writer may replace the accounting DB.
        for path in ('/var/lock/xray-usage-v1.lock','/var/lib/xray-xhttp443/control.lock','/var/lock/xray-usage.lock'):
            path=pathlib.Path(path)
            if not path.parent.exists():continue
            handle=stack.enter_context(path.open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def validate(component, candidate):
    run([str(candidate),'version'])
    if component=='xray':
        run([str(candidate),'run','-test','-confdir','/etc/v2ray-agent/xray/conf'])
        run([str(candidate),'run','-test','-config','/etc/xray-xhttp443/current/config.json'])

def save_journal(folder, record):
    atomic(folder/'journal.json',(json.dumps(record,indent=2)+'\n').encode(),0o600)

def replace_transaction(component, binaries, folder, info, expected=None):
    spec=CORES[component]
    with locks():
        if expected and any(sha(pathlib.Path(path).read_bytes())!=digest for path,digest in expected.items()):
            raise ValueError('Core changed before rollback; refusing overwrite')
        for unit in spec['units']:run(['systemctl','is-active','--quiet',unit])
        # Persist counter tails before stopping any service; abort on partial sampling.
        run(['/usr/bin/python3','/root/xray-usage-collect.py'])
        record={'component':component,'release':info,'status':'prepared','files':[]}
        for index,path in enumerate(spec['files']):
            target=pathlib.Path(path)
            if target.is_symlink() or not target.is_file():raise ValueError('Unexpected core target')
            old=target.read_bytes()
            atomic(folder/('before-'+str(index)),old,0o600)
            record['files'].append({'path':path,'before':sha(old),'after':sha(binaries[path]),'backup':'before-'+str(index)})
        save_journal(folder,record)
        try:
            record['status']='applying';save_journal(folder,record)
            for item in record['files']:
                if sha(pathlib.Path(item['path']).read_bytes())!=item['before']:
                    raise ValueError('Concurrent binary change')
                atomic(item['path'],binaries[item['path']])
            for unit in spec['units']:run(['systemctl','restart',unit])
            time.sleep(2)
            for unit in spec['units']:run(['systemctl','is-active','--quiet',unit])
            run(['/usr/bin/python3','/root/xray-usage-collect.py'])
            record['status']='committed';save_journal(folder,record)
        except BaseException:
            conflict=False
            for item in record['files']:
                current=sha(pathlib.Path(item['path']).read_bytes())
                if current==item['after']:
                    atomic(item['path'],(folder/item['backup']).read_bytes())
                elif current!=item['before']:conflict=True
            if not conflict:
                for unit in spec['units']:
                    try:run(['systemctl','restart',unit])
                    except Exception:conflict=True
            record['status']='rollback-needs-review' if conflict else 'rolled-back'
            save_journal(folder,record)
            raise RuntimeError('Core change failed; status='+record['status']+'; inspect '+str(folder)) from None
    print('Core change committed. Backup ID:',folder.name)
    print('Verify real clients and the next V1 sample. Configuration and historical data were not restored.')

def upgrade(component, version):
    require_install()
    info=release(component,version)
    folder=STATE/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(mode=0o700)
    with urllib.request.urlopen(urllib.request.Request(info['url'],headers={'User-Agent':'LA-Portal-core-update'}),timeout=90) as response:
        data=payload(info,response.read(100_000_001))
    if all(pathlib.Path(path).is_file() and sha(pathlib.Path(path).read_bytes())==sha(data) for path in CORES[component]['files']):
        print('Requested binary is already installed; no restart needed')
        return
    candidate=folder/'candidate';atomic(candidate,data)
    validate(component,candidate)
    replace_transaction(component,{path:data for path in CORES[component]['files']},folder,info)

def rollback(backup):
    require_install()
    if not re.fullmatch(r'[0-9TZ]+-[0-9a-f]{8}',backup or ''):
        raise ValueError('Use the printed backup ID')
    source=STATE/backup
    record=json.loads((source/'journal.json').read_text())
    if record['status']!='committed':raise ValueError('Backup is not a committed change')
    component=record['component'];spec=CORES[component]
    if {f['path'] for f in record['files']}!=set(spec['files']):raise ValueError('Unexpected backup targets')
    binaries={}
    for item in record['files']:
        if not re.fullmatch(r'before-\d+',item['backup']):raise ValueError('Unexpected backup file')
        data=(source/item['backup']).read_bytes()
        if sha(data)!=item['before'] or sha(pathlib.Path(item['path']).read_bytes())!=item['after']:
            raise ValueError('Backup/current binary hash changed; refusing overwrite')
        binaries[item['path']]=data
    folder=STATE/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(mode=0o700)
    for index,(path,data) in enumerate(binaries.items()):
        candidate=folder/('candidate-'+str(index));atomic(candidate,data);validate(component,candidate)
    replace_transaction(component,binaries,folder,{'rollback_of':backup},expected={item['path']:item['after'] for item in record['files']})

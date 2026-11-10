"""Exercise installed protocol clients locally; credentials never leave this host."""
import importlib.util
import ipaddress
import json
import pathlib
import socket
import subprocess
import tempfile
import time

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]

def xray_client(inbound, port, tls_name, public_key):
    settings=inbound['streamSettings'];user=inbound['settings']['clients'][0]
    credential={'id':user['id'],'encryption':'none'}
    stream={'network':settings['network'],'security':settings['security']}
    if settings['security']=='reality':
        reality=settings['realitySettings'];credential['flow']=user['flow']
        stream['realitySettings']={'serverName':reality['serverNames'][0],
            'publicKey':public_key(reality['privateKey']),'shortId':reality['shortIds'][0],'fingerprint':'chrome'}
    else:
        stream['tlsSettings']={'serverName':tls_name,'allowInsecure':False,'alpn':['h2']}
        stream['xhttpSettings']=settings['xhttpSettings']
    return {'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{}}],
        'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':'127.0.0.1','port':inbound['port'],'users':[credential]}]},'streamSettings':stream}]}

def run():
    spec=importlib.util.spec_from_file_location('userm_test','/opt/vps-modules/userm/1.0.1/userm.py')
    userm=importlib.util.module_from_spec(spec);spec.loader.exec_module(userm)
    read=lambda p:json.loads(pathlib.Path(p).read_text())
    main=read('/etc/v2ray-agent/xray/conf/portal.json')
    xhttp=read('/etc/xray-xhttp443/current/config.json')
    hy2=read('/etc/hy2-udp443/server.private.json')
    settings=read('/etc/userm/settings.json')
    tls_name=settings['endpoints']['xray-xhttp443.service:XHTTP443']['sni']
    reality=next(i for i in main['inbounds'] if i.get('streamSettings',{}).get('security')=='reality')
    xhttp=next(i for i in xhttp['inbounds'] if i['protocol']=='vless')
    results=[]
    with tempfile.TemporaryDirectory(prefix='la-protocol-test-') as tmp:
        folder=pathlib.Path(tmp);folder.chmod(0o700)
        for name,inbound in (('reality',reality),('xhttp',xhttp),('hy2',None)):
            port=free_port();config=folder/(name+'.json')
            if name=='hy2':
                login,password=next(iter(hy2['auth']['userpass'].items()))
                value={'server':'127.0.0.1:443','auth':login+':'+password,
                    'tls':{'sni':tls_name,'insecure':False},'socks5':{'listen':'127.0.0.1:'+str(port)}}
                args=['/usr/local/lib/hy2-udp443/hysteria','client','--config',str(config),'--disable-update-check']
            else:
                value=xray_client(inbound,port,tls_name,userm.public_key)
                args=['/etc/v2ray-agent/xray/xray','run','-config',str(config)]
            config.write_text(json.dumps(value));config.chmod(0o600)
            process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                ready=False
                for _ in range(50):
                    if process.poll() is not None:break
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.2):ready=True;break
                    except OSError:time.sleep(.1)
                if not ready:raise RuntimeError(name+' test client did not start')
                response=subprocess.run(['curl','--fail','--silent','--show-error','--socks5-hostname',
                    '127.0.0.1:'+str(port),'--connect-timeout','8','--max-time','20','https://api.ipify.org'],
                    capture_output=True,text=True,timeout=25)
                if response.returncode:raise RuntimeError(name+' authenticated proxy request failed')
                ipaddress.ip_address(response.stdout.strip())
                results.append({'protocol':name,'passed':True})
                print('PROTOCOL_PASS',name,flush=True)
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
    # Trigger the same V1 service used by the scheduled timer, after real traffic.
    subprocess.run(['systemctl','start','la-usage.service'],check=True)
    result=pathlib.Path('/var/lib/la-portal/self-test.json')
    result.write_text(json.dumps({'protocols':results,'collection':'PASS'},indent=2));result.chmod(0o600)
    print('PROTOCOL_AND_COLLECTION_PASS')

if __name__=='__main__':run()

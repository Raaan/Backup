import importlib.util
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import portal
from scripts import certificates, mail_test, mail_setup, setup
sys.path.insert(0,str(ROOT/'modules/usage/v1_core'))
from counter_engine import ingest
from reporting import Reporting
from modules.usage.v1_core import billing
from scripts import billing_update

class DeploymentTests(unittest.TestCase):
    def test_setup_retries_invalid_timezone_before_quota_and_saves_configuration(self):
        answers = ['owner@example.com', 'test-01', 'dl.google.com',
                   'zerouser', '10', '', '+8', 'UTC', '600', 'GB', '1', 'YES']
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'profile.json'
            with mock.patch.object(setup, 'detect_public_ipv4', return_value='192.0.2.1'), \
                 mock.patch('builtins.input', side_effect=answers), mock.patch('builtins.print') as output:
                setup.configure(path)
            c = json.loads(path.read_text())
            self.assertEqual(c['billing'], {'day':10, 'time':'00:10', 'timezone':'UTC',
                                           'quota':'600', 'unit':'GB', 'factor':'1'})
            self.assertEqual(c['users'], ['zerouser'])
            self.assertEqual(c['host'], '192.0.2.1')
            self.assertEqual(c['tls_name'], c['host'])
            self.assertTrue(any('Please enter this field again' in str(call) for call in output.call_args_list))

    def test_public_ip_discovery_rejects_private_and_disagreeing_addresses(self):
        def response(value):
            r = mock.MagicMock()
            r.__enter__.return_value.read.return_value = value.encode('ascii')
            return r
        with mock.patch.object(setup.urllib.request, 'build_opener') as build:
            opener = build.return_value
            opener.open.side_effect = [response('8.8.8.8'), response('8.8.8.8')]
            self.assertEqual(setup.detect_public_ipv4(), '8.8.8.8')
            opener.open.side_effect = [response('8.8.8.8'), response('1.1.1.1')]
            self.assertIsNone(setup.detect_public_ipv4())
            opener.open.side_effect = [response('10.0.0.1'), OSError('timeout')]
            self.assertIsNone(setup.detect_public_ipv4())
            opener.open.side_effect = [OSError('timeout'), response('8.8.8.8')]
            self.assertEqual(setup.detect_public_ipv4(), '8.8.8.8')

    def test_setup_falls_back_to_manual_host_when_discovery_fails(self):
        answers = ['192.0.2.1', 'owner@example.com', '', 'dl.google.com',
                   '', '', '', '', '', '', '', 'YES']
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'profile.json'
            with mock.patch.object(setup, 'detect_public_ipv4', return_value=None), \
                 mock.patch('builtins.input', side_effect=answers), mock.patch('builtins.print'):
                setup.configure(path)
            c = json.loads(path.read_text())
            self.assertEqual(c['host'], '192.0.2.1')
            self.assertEqual(c['tls_name'], c['host'])
            self.assertEqual(c['billing']['timezone'], 'Asia/Hong_Kong')

    def test_billing_policy_calendar_units_and_invalid_inputs(self):
        c=billing.normalize({'day':17,'time':'08:30','timezone':'UTC','quota':'500','unit':'GB','factor':'1.5'})
        self.assertEqual(billing.calendar(c),'*-*-17 08:30:00 UTC')
        self.assertEqual(billing.quota_bytes(c),500_000_000_000)
        self.assertEqual(billing.estimated_bytes(c,60),90)
        for bad in ({'day':0},{'day':29},{'time':'24:00'},{'quota':0},{'quota':'NaN'},{'factor':True},{'unit':'TB'}):
            with self.assertRaises((ValueError,KeyError)):billing.normalize(bad)

    def test_billing_edit_changes_timer_without_reset_or_proxy_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            record=root/'install.json';record.write_text('{}')
            policy=root/'billing.json';policy.write_text(json.dumps(billing.DEFAULT))
            fake_lock=SimpleNamespace(flock=lambda *args:None,LOCK_EX=1,LOCK_NB=2)
            with mock.patch.multiple(billing_update,INSTALL_RECORD=record,CONFIG=policy,
                  LOCK=root/'lock',DROPIN=root/'timer.d/billing.conf',BACKUPS=root), \
                 mock.patch.dict(sys.modules,{'fcntl':fake_lock}), \
                 mock.patch.object(billing_update.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run, \
                 mock.patch.object(billing_update.subprocess,'check_output',return_value='inactive'):
                billing_update.apply_settings(billing.normalize({'day':17,'time':'08:30','quota':'500','unit':'GB'}))
                self.assertIn('OnCalendar=*-*-17 08:30:00 UTC',(root/'timer.d/billing.conf').read_text())
                self.assertEqual(json.loads(policy.read_text())['quota'],'500')
                calls=[call.args[0] for call in run.call_args_list]
                self.assertIn(['systemctl','clean','--what=state','la-monthly.timer'],calls)
                self.assertTrue(all('xray.service' not in c and '-reset' not in c for c in calls))
                run.reset_mock()
                billing_update.apply_settings(billing.normalize({'day':17,'time':'08:30','quota':'900','unit':'GB'}))
                self.assertEqual(len(run.call_args_list),1) # is-active only; quota edit leaves timer alone

    def test_mail_config_is_independent_and_rejects_newline_injection(self):
        c={'host':'smtp.gmail.com','port':587,'user':'sender@example.com','password':'test-secret',
           'from':'sender@example.com','to':'recipient@example.com'}
        text=mail_setup.smtp_config(c)
        self.assertIn('tls_starttls on',text)
        self.assertIn('account portal',text)
        c['password']='bad\naccount injected'
        with self.assertRaises(ValueError):mail_setup.smtp_config(c)

    def test_acme_ip_domain_and_explicit_terms(self):
        c={'tls_name':'192.0.2.1','certificate':{'mode':'acme','email':'owner@example.com','agree_tos':True}}
        args=certificates.issue_argv(c,'staging')
        self.assertIn('--ip-address',args)
        self.assertIn('shortlived',args)
        self.assertIn('--staging',args)
        c['tls_name']='node.example.com'
        args=certificates.issue_argv(c)
        self.assertIn('-d',args)
        self.assertNotIn('--staging',args)
        self.assertNotIn('--ip-address',args)
        c['certificate']['agree_tos']=False
        with self.assertRaises(ValueError):certificates.issue_argv(c)

    def test_managed_config_without_preexisting_certificates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'config.json'
            c={'server_id':'test','host':'192.0.2.1','tls_name':'192.0.2.1',
               'reality_sni':'example.com','reality_target':'example.com:443','users':['Owner'],
               'certificate':{'mode':'acme','email':'owner@example.com','agree_tos':True},'mail':None}
            path.write_text(json.dumps(c))
            with mock.patch('subprocess.run') as runner:
                parsed=portal.read_config(path)
                runner.assert_not_called()
                with mock.patch.object(certificates,'BASE',pathlib.Path(tmp)/'acme'):
                    certificates.prepare(parsed)
                self.assertEqual(runner.call_count,4)
                self.assertIn('certbot==5.4.0',runner.call_args_list[1].args[0])
                self.assertIn('--staging',runner.call_args_list[2].args[0])
                self.assertNotIn('--staging',runner.call_args_list[3].args[0])
            self.assertIn('fullchain.pem',parsed['tls_cert'])
            self.assertIn('renew',parsed['certificate_renewal_command'])

    def test_smtp_test_marker_only_on_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=pathlib.Path(tmp)
            config=folder/'mail.json'
            config.write_text(json.dumps({'from':'sender@example.com','to':'recipient@example.com'}))
            runner=mock.Mock(return_value=SimpleNamespace(returncode=1))
            with self.assertRaises(RuntimeError):mail_test.send(config,folder/'state',runner)
            self.assertFalse((folder/'state/smtp-tested').exists())
            runner.return_value=SimpleNamespace(returncode=0)
            mail_test.send(config,folder/'state',runner)
            self.assertTrue((folder/'state/smtp-tested').is_file())
            self.assertIn(b'LA Portal SMTP configuration test',runner.call_args.kwargs['input'])

    def test_stage_modules_no_real_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=pathlib.Path(tmp)
            cert=folder/'cert';key=folder/'key'
            cert.write_text('TEST CERTIFICATE');key.write_text('TEST KEY')
            c={'server_id':'test-vps','host':'192.0.2.1','tls_name':'192.0.2.1','tls_cert':str(cert),'tls_key':str(key),
               'reality_sni':'test.example','reality_target':'test.example:443','users':['Owner'],
               'mail':None,'enable_monthly':False,'billing':{'day':17,'time':'08:30','quota':'500','unit':'GB','factor':'1.5'}}
            config=folder/'config.json';config.write_text(json.dumps(c))
            c=portal.read_config(config)
            stage=folder/'stage';manifest=portal.stage(c,stage)
            self.assertFalse(manifest['mail_enabled'])
            self.assertIn('OnCalendar=*-*-17 08:30:00 UTC',(stage/'etc/systemd/system/la-monthly.timer').read_text())
            self.assertEqual(json.loads((stage/'etc/la-portal/billing.json').read_text())['quota'],'500')
            self.assertFalse((stage/'etc/la-portal/msmtprc').exists())
            self.assertFalse((stage/'etc/systemd/system/la-weekly.timer').exists())
            self.assertFalse((stage/'opt/la-portal/weekly.py').exists())
            self.assertNotIn('/etc/systemd/system/cloudflared.service',manifest['files'])
            main=json.loads((stage/'etc/v2ray-agent/xray/conf/portal.json').read_text())
            xhttp=json.loads((stage/'etc/xray-xhttp443/current/config.json').read_text())
            ids=lambda inbound:{c['id'] for c in inbound['settings']['clients']}
            self.assertEqual(ids(main['inbounds'][1]),ids(xhttp['inbounds'][1]))
            self.assertEqual(main['inbounds'][0]['listen'],'127.0.0.1')
            self.assertEqual(xhttp['inbounds'][0]['port'],10086)
            reality=[i for i in main['inbounds'] if i.get('streamSettings',{}).get('security')=='reality']
            self.assertEqual([i['port'] for i in reality],[443])
            self.assertEqual(xhttp['inbounds'][1]['port'],8443)
            self.assertNotIn(8443,[i['port'] for i in main['inbounds']])
            # Exercise userm's real link exporter with synthetic XHTTP settings.
            import ast
            import urllib.parse
            source=ast.parse((ROOT/'modules/userm/userm.py').read_text(encoding='utf-8-sig'))
            function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='connection')
            namespace={'urllib':urllib}
            exec(compile(ast.Module(body=[function],type_ignores=[]),'userm-connection','exec'),namespace)
            inbound=xhttp['inbounds'][1]
            endpoint={'adapter':SimpleNamespace(unit='xray-xhttp443.service'),'inbound':inbound}
            settings=json.loads((stage/'etc/userm/settings.json').read_text())
            link=namespace['connection'](endpoint,inbound['settings']['clients'][0],settings)
            uri=urllib.parse.urlparse(link)
            self.assertEqual(uri.port,8443)
            query=urllib.parse.parse_qs(uri.query)
            self.assertEqual(query['security'],['tls'])
            self.assertEqual(query['sni'],['192.0.2.1'])
            self.assertNotIn('allowInsecure',query)
            self.assertTrue((stage/'etc/hy2-udp443/accounts.private.json').exists())
            self.assertTrue((stage/'opt/vps-modules/userm/1.0.1/userm.py').exists())
            with self.assertRaises(FileExistsError):portal.stage(c,stage)
            c['users']=['Owner','owner']
            config.write_text(json.dumps(c))
            with self.assertRaises(ValueError):portal.read_config(config)

    def test_zero_history_counter_reporting_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=pathlib.Path(tmp)
            sample={'last_updated':'2026-11-10T05:00:00+00:00','period_started':'old',
              'users':{'California,Owner':{'uplink':999,'downlink':0}},
              'collector_generations':{'main':'g1','xhttp443':'g2','hy2':'g3'},
              'last_counters':{'user>>>California,Owner>>>traffic>>>uplink':100},
              'xhttp443_last_counters':{},'hy2_last_counters':{}}
            (folder/'sample.json').write_text(json.dumps(sample))
            (folder/'identities.json').write_text(json.dumps({'California,Owner':'Owner'}))
            cmd=[sys.executable,str(ROOT/'scripts/init-new-v1.py'),'--schema',str(ROOT/'modules/usage/schema.sql'),
              '--sample',str(folder/'sample.json'),'--identities',str(folder/'identities.json'),'--out',str(folder/'stage')]
            subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL)
            self.assertNotEqual(subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode,0)
            db=sqlite3.connect(folder/'stage/usage.db');db.execute('PRAGMA foreign_keys=ON')
            self.assertEqual(db.execute('SELECT SUM(total_bytes) FROM v1_account_period').fetchone()[0],0)
            args=dict(db=db,source_id='fair-light-1/xray-main',batch_id='test1',generation='g1',
              observed_at='2026-11-10T05:01:00+00:00',counters=[('California,Owner','uplink',150)],
              period_id=sample['last_updated'],trusted_prefixes=['California'])
            self.assertEqual(ingest(**args),('APPLIED',50))
            self.assertEqual(ingest(**args),('DUPLICATE',0))
            args['counters']=[('California,Owner','uplink',160)]
            with self.assertRaises(ValueError):ingest(**args)
            args.update(batch_id='test2',generation='new-process',observed_at='2026-11-10T05:02:00+00:00',counters=[('California,Owner','uplink',10)])
            self.assertEqual(ingest(**args),('APPLIED',10))
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
            db.close()
            report=Reporting(folder/'stage/usage.db')
            self.assertEqual(report.monthly_report()['monthly_total_bytes'],60)
            report.close()
            custom=Reporting(folder/'stage/usage.db',billing={'quota':'500','unit':'GB','factor':'1.5'})
            result=custom.monthly_report();custom.close()
            self.assertEqual(result['estimated_vps_bytes'],90)
            self.assertEqual(result['quota_bytes'],500_000_000_000)
            self.assertEqual(result['remaining_bytes'],500_000_000_000-90)
            # Exercise the real monthly transition function against a local copy.
            import ast
            import datetime
            tree=ast.parse((ROOT/'modules/usage/monthly.py').read_text(encoding='utf-8-sig'))
            functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('active','validate','transition')]
            namespace={'datetime':datetime.datetime,'MAIN':'fair-light-1/xray-main'}
            exec(compile(ast.Module(body=functions,type_ignores=[]),'monthly-functions','exec'),namespace)
            with sqlite3.connect(folder/'stage/usage.db') as conn:
                namespace['transition'](conn,sample['last_updated'],'2026-12-08T00:10:00+00:00')
                self.assertEqual(namespace['active'](conn),'2026-12-08T00:10:00+00:00')
                self.assertEqual(conn.execute('SELECT SUM(last_counter_bytes) FROM source_counter_state WHERE source_id=?',('fair-light-1/xray-main',)).fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT SUM(total_bytes) FROM v1_account_period WHERE period_id=?',(sample['last_updated'],)).fetchone()[0],60)
            conn.close()

if __name__=='__main__':unittest.main()

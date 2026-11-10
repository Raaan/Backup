import ast
import datetime
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'modules/usage'))
sys.path.insert(0, str(ROOT/'modules/usage/v1_core'))
from lifecycle import read_inventory, synchronize, retire_absent
from reporting import Reporting
from shadow_reconcile import SOURCES


class LifecycleTests(unittest.TestCase):
    def test_fusion_removal_next_period_history_and_readdition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'usage.db'
            db = sqlite3.connect(path)
            try:
                db.execute('PRAGMA foreign_keys=ON')
                db.executescript((ROOT/'modules/usage/schema.sql').read_text())
                old = '2026-11-10T00:10:00+00:00'
                new = '2026-12-10T00:10:00+00:00'
                db.execute('INSERT INTO billing_periods VALUES (?,?,NULL,0)', (old,old))
                db.execute("INSERT INTO v1_live_meta VALUES ('last_state_at',?)",(old,))
                for sid,*_ in SOURCES:
                    db.execute('INSERT INTO sources VALUES (?,?,?,?,?,1)',(sid,'test','test',None,None))
                identities = [(SOURCES[0][0],'LAY,RAAN'), (SOURCES[1][0],'Other，Raan-vless_reality_vision'),
                              (SOURCES[2][0],'HY,raan')]
                synchronize(db,old,identities)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],1)
                aid = db.execute('SELECT account_id FROM accounts').fetchone()[0]
                db.execute('INSERT INTO source_counter_state VALUES (?,?,?,?,?,?)',
                           (SOURCES[0][0],'LAY,RAAN','uplink','g',30,old))
                db.execute('INSERT INTO source_hourly_usage VALUES (?,?,?,?,?,?,?)',
                           (old,old,SOURCES[0][0],'LAY,RAAN',aid,30,0))
                synchronize(db,old,identities[1:])
                self.assertIsNone(db.execute('SELECT absent_since_period FROM v1_account_lifecycle').fetchone()[0])
                synchronize(db,old,[])
                self.assertEqual(db.execute('SELECT status FROM accounts').fetchone()[0],'active')
                db.commit()
                # Exercise the actual monthly transition, not only the helper.
                tree=ast.parse((ROOT/'modules/usage/monthly.py').read_text(encoding='utf-8-sig'))
                functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('active','validate','transition')]
                ns={'datetime':datetime.datetime,'MAIN':SOURCES[0][0],'retire_absent':retire_absent}
                exec(compile(ast.Module(body=functions,type_ignores=[]),'monthly-transition','exec'),ns)
                ns['transition'](db,old,new)
                self.assertEqual(db.execute('SELECT status FROM accounts').fetchone()[0],'inactive')
                report=Reporting(path,billing={})
                try:
                    self.assertEqual(report.accounts(new),[])
                    self.assertEqual(report.accounts(old)[0]['total_bytes'],30)
                    self.assertEqual(report.weekly_window('2026-12-11T00:00:00+00:00')['accounts'],[])
                finally:
                    report.close()
                synchronize(db,new,[(SOURCES[2][0],'Different，rAaN')])
                db.commit()
                self.assertEqual(db.execute('SELECT account_id,status FROM accounts').fetchall(),[(aid,'active')])
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            finally:
                db.close()

    def test_inventory_fails_closed_and_reads_actual_active_configurations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            def write(path,value):
                p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value))
            def config(raws):
                return {'inbounds':[{'protocol':'vless','settings':{'clients':[{'email':raw} for raw in raws]}}]}
            write('etc/v2ray-agent/xray/conf/portal.json',config(['LAY,RAAN']))
            write('etc/xray-xhttp443/current/config.json',config([]))
            write('etc/hy2-udp443/server.private.json',{'auth':{'type':'userpass','userpass':{'raan':'synthetic'}}})
            write('etc/hy2-udp443/accounts.private.json',{'users':{'Raan':{'counter_user':'HY，Raan'}}})
            self.assertEqual(read_inventory(root),[(SOURCES[0][0],'LAY,RAAN'),(SOURCES[2][0],'HY，Raan')])
            write('etc/v2ray-agent/xray/conf/portal.json',config([]))
            self.assertEqual(read_inventory(root),[(SOURCES[2][0],'HY，Raan')])
            (root/'etc/xray-xhttp443/current/config.json').unlink()
            with self.assertRaises(FileNotFoundError):
                read_inventory(root)


if __name__ == '__main__':
    unittest.main()

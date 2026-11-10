import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'modules/usage'))
sys.path.insert(0, str(ROOT/'modules/usage/v1_core'))
import live_ingest


@unittest.skipUnless(sys.platform == 'linux', 'Atomic replacement of an open SQLite database requires Linux')
class LiveIngestTests(unittest.TestCase):
    def run_new_identity(self, raw, explicit=False, unmapped=False, fused=False):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            state = folder/'sample.json'
            identities = folder/'identities.json'
            initial = 'California,Owner-vless_reality_vision'
            sample = {'last_updated':'2026-11-10T05:00:00+00:00',
                      'collector_generations':{'main':'g1','xhttp443':'g2','hy2':'g3'},
                      'last_counters':{'user>>>'+initial+'>>>traffic>>>uplink':100},
                      'xhttp443_last_counters':{}, 'hy2_last_counters':{}}
            state.write_text(json.dumps(sample))
            identities.write_text(json.dumps({initial:'Owner'}))
            out = folder/'baseline'
            subprocess.run([sys.executable, str(ROOT/'scripts/init-new-v1.py'),
                            '--schema',str(ROOT/'modules/usage/schema.sql'),
                            '--sample',str(state),'--identities',str(identities),
                            '--out',str(out)],check=True,stdout=subprocess.DEVNULL)
            dbpath = out/'usage.db'
            state = out/'current.json'
            if explicit:
                with sqlite3.connect(dbpath) as db:
                    db.execute("INSERT INTO accounts VALUES ('acc_newuser','NewUser','active')")
                    db.execute("INSERT INTO account_aliases VALUES (?,'acc_newuser','explicit')",(raw,))
                db.close()
            sample = json.loads(state.read_text())
            sample['last_updated'] = '2026-11-10T05:01:00+00:00'
            sample['last_counters']['user>>>'+initial+'>>>traffic>>>uplink'] = 110
            sample['last_counters']['user>>>'+raw+'>>>traffic>>>uplink'] = 25
            sample['users'] = {initial:{'uplink':10,'downlink':0},raw:{'uplink':25,'downlink':0}}
            if fused:
                other = 'Other，newuser-vless_reality_vision'
                sample['xhttp443_last_counters']['user>>>'+other+'>>>traffic>>>downlink'] = 30
                sample['users'][other] = {'uplink':0,'downlink':30}
            state.write_text(json.dumps(sample))
            before = dbpath.read_bytes()
            with mock.patch.multiple(live_ingest, DB=dbpath, STATE=state), mock.patch('builtins.print'):
                if unmapped:
                    with self.assertRaisesRegex(RuntimeError, 'Unmapped traffic'):
                        live_ingest.run()
                    self.assertEqual(dbpath.read_bytes(), before)
                    return
                live_ingest.run()
                live_ingest.run()  # Replaying the same snapshot must add nothing.
            with sqlite3.connect(dbpath) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('SELECT SUM(total_bytes) FROM v1_account_period').fetchone()[0],65 if fused else 35)
                self.assertEqual(db.execute("SELECT total_bytes FROM v1_account_period WHERE canonical_name='NewUser'").fetchone()[0],55 if fused else 25)
            db.close()

    def test_new_trusted_identity_is_registered_before_counter_and_counted_once(self):
        self.run_new_identity('California,NewUser')

    def test_untrusted_identity_does_not_change_live_database(self):
        self.run_new_identity('UnlabelledUnknown', unmapped=True)

    def test_custom_server_label_is_automatically_fused(self):
        self.run_new_identity('LAY,NewUser')

    def test_fullwidth_comma_and_protocol_suffix_are_normalized(self):
        self.run_new_identity('LAY，NewUser-vless_reality_vision')

    def test_sources_labels_and_case_merge_to_one_account_without_replay_duplicates(self):
        self.run_new_identity('LAY,NewUser', fused=True)

    def test_explicit_alias_allows_custom_prefix_without_trusting_it_globally(self):
        self.run_new_identity('LAY,NewUser', explicit=True)


if __name__ == '__main__':
    unittest.main()

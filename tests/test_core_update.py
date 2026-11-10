import contextlib
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import core_update as core

class CoreTests(unittest.TestCase):
    def test_verified_asset_and_xray_extraction(self):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:archive.writestr('xray',b'test-xray')
        data=buffer.getvalue()
        info={'component':'xray','sha256':hashlib.sha256(data).hexdigest()}
        self.assertEqual(core.payload(info,data),b'test-xray')
        with self.assertRaises(ValueError):core.payload(info,b'wrong')

    def test_official_release_digest_required(self):
        release={'tag_name':'v26.10.31','assets':[{'name':'Xray-linux-64.zip','digest':'sha256:'+'a'*64,
          'browser_download_url':'https://github.com/XTLS/Xray-core/releases/download/v26.10.31/Xray-linux-64.zip'}]}
        def response():return io.BytesIO(json.dumps(release).encode())
        with mock.patch.object(core.urllib.request,'urlopen',side_effect=lambda *a,**k:response()):
            self.assertEqual(core.release('xray','26.10.31')['version'],'v26.10.31')
            release['assets'][0]['digest']=None
            with self.assertRaises(ValueError):core.release('xray','26.10.31')
        with self.assertRaises(ValueError):core.release('xray','latest')

    def transaction(self, fail):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            targets=[root/'main',root/'xhttp']
            for path in targets:path.write_bytes(b'old-core')
            folder=root/'backup';folder.mkdir()
            spec={'files':[str(p) for p in targets],'units':['main-test.service','xhttp-test.service']}
            calls=[];failed=False
            def run(args):
                nonlocal failed
                calls.append(args)
                if fail and args[:2]==['systemctl','restart'] and not failed:
                    failed=True;raise RuntimeError('synthetic startup failure')
                return ''
            def atomic(path,data,mode=0o755):pathlib.Path(path).write_bytes(data)
            with mock.patch.dict(core.CORES,{'xray':spec}), \
                 mock.patch.object(core,'locks',contextlib.nullcontext), \
                 mock.patch.object(core,'run',side_effect=run), \
                 mock.patch.object(core,'atomic',side_effect=atomic), \
                 mock.patch.object(core.time,'sleep'):
                operation=lambda:core.replace_transaction('xray',{str(p):b'new-core' for p in targets},folder,{'version':'test'})
                if fail:
                    with self.assertRaises(RuntimeError):operation()
                else:operation()
            journal=json.loads((folder/'journal.json').read_text())
            self.assertEqual(journal['status'],'rolled-back' if fail else 'committed')
            for p in targets:self.assertEqual(p.read_bytes(),b'old-core' if fail else b'new-core')
            self.assertTrue(all('daemon-reload' not in args and '-reset' not in args for args in calls))
            self.assertTrue(all(args[-1] in spec['units'] for args in calls if args[:2]==['systemctl','restart']))

    def test_rollback_refuses_concurrent_core_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);target=root/'core';target.write_bytes(b'changed')
            with mock.patch.object(core,'locks',contextlib.nullcontext), mock.patch.object(core,'run') as run:
                with self.assertRaises(ValueError):
                    core.replace_transaction('xray',{},root,{},expected={str(target):core.sha(b'old')})
                run.assert_not_called()

    def test_upgrade_only_restarts_target_and_preserves_config(self):self.transaction(False)
    def test_failed_start_restores_previous_binaries(self):self.transaction(True)

if __name__=='__main__':unittest.main()

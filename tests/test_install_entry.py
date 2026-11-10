import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]

class InstallEntryTests(unittest.TestCase):
    def test_shell_source_uses_lf(self):
        self.assertNotIn(b'\r', (ROOT/'install.sh').read_bytes())
        self.assertIn('eol=lf',(ROOT/'.gitattributes').read_text())

    @unittest.skipUnless(sys.platform=='linux','Shell integration runs on Linux')
    def test_existing_config_is_reused_and_yes_reaches_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);bin=root/'bin';bin.mkdir()
            script=root/'install.sh';shutil.copyfile(ROOT/'install.sh',script)
            config=root/'private config.json';config.write_text('{}')
            log=root/'calls'
            (bin/'id').write_text('#!/bin/sh\necho 0\n');(bin/'id').chmod(0o755)
            (bin/'python3').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n');(bin/'python3').chmod(0o755)
            env=dict(os.environ,PATH=str(bin)+':'+os.environ['PATH'],CALLS=str(log))
            result=subprocess.run(['sh',str(script),'--config',str(config),'--yes'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            calls=log.read_text()
            self.assertNotIn('setup --config',calls)
            self.assertIn('plan --config',calls)
            self.assertIn('install --config',calls)
            self.assertIn('--apply',calls)

    @unittest.skipUnless(sys.platform=='linux','Shell integration runs on Linux')
    def test_yes_requires_existing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);bin=root/'bin';bin.mkdir()
            (bin/'id').write_text('#!/bin/sh\necho 0\n');(bin/'id').chmod(0o755)
            result=subprocess.run(['sh',str(ROOT/'install.sh'),'--config',str(root/'absent'),'--yes'],
                env=dict(os.environ,PATH=str(bin)+':'+os.environ['PATH']),capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn('complete private config',result.stderr)

if __name__=='__main__':unittest.main()

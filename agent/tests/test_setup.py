"""Installation invariants with temporary profiles; no accounts or live submissions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import browser
import file_lock
import profile_tools
import release
import runtime
import setup_project

class SetupTests(unittest.TestCase):
    def test_config_merge_preserves_unrelated_servers_and_sets_client_deadlines(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            setup_project.write_json(root/'.mcp.json', {'mcpServers':{'existing':{'command':'keep-me'}}})
            setup_project.write_json(root/'.claude/settings.local.json',{'env':{'MY_SETTING':'keep'}})
            entries={'linkedin':{'command':'python','args':['script.py']}}
            with patch.object(setup_project,'ROOT',root),patch.object(setup_project,'config_entries',return_value=entries):
                setup_project.configure()
                setup_project.configure()
            claude=json.loads((root/'.mcp.json').read_text())
            self.assertEqual(claude['mcpServers']['existing']['command'],'keep-me')
            self.assertEqual(claude['mcpServers']['linkedin']['timeout'],7200000)
            kimi=json.loads((root/'.kimi-code/mcp.json').read_text())
            self.assertEqual(kimi['mcpServers']['linkedin']['toolTimeoutMs'],7200000)
            self.assertEqual(json.loads((root/'.claude/settings.local.json').read_text())['env']['MY_SETTING'],'keep')
            self.assertTrue((root/'data/setup-backups/.mcp.json').exists())

    def test_runtime_rejects_public_debugging_endpoint(self):
        for endpoint in ['http://0.0.0.0:9222','http://example.com:9222','https://127.0.0.1:9222','http://user@127.0.0.1:9222']:
            with self.subTest(endpoint=endpoint),patch.object(runtime,'settings',return_value={'browser_endpoint':endpoint}):
                with self.assertRaises(ValueError):runtime.browser_endpoint()

    def test_verified_locations_extend_without_keyword_fallback(self):
        with patch.object(runtime,'settings',return_value={'linkedin_geo_ids':{'Test location':'123456'}}):
            self.assertEqual(runtime.geo_ids()['Test location'],'123456')
        with patch.object(runtime,'settings',return_value={'linkedin_geo_ids':{'Test location':'invented'}}):
            with self.assertRaises(ValueError):runtime.geo_ids()

    def test_browser_refuses_unknown_endpoint_owner(self):
        with patch.object(browser,'probe',return_value={'Browser':'Chrome'}),patch.object(browser,'owned_process',return_value=None):
            with self.assertRaisesRegex(ValueError,'unverified browser'):browser.start()

    def test_cv_import_preserves_original_and_refuses_silent_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'given.txt';source.write_text('Example candidate — خبرة',encoding='utf-8')
            with patch.object(profile_tools,'ROOT',root):
                profile_tools.import_cv(source)
                self.assertEqual((root/'profile/cv/resume.txt').read_bytes(),source.read_bytes())
                source.write_text('Changed document',encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'different resume'):profile_tools.import_cv(source)

    def test_missing_profile_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(profile_tools,'ROOT',Path(temp)):
            self.assertFalse(profile_tools.validate())

    def test_locks_exclude_a_second_process_and_release(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'lock'
            code='''import sys
sys.path.insert(0,sys.argv[1])
import file_lock as lock
with open(sys.argv[2],'a') as f:
 try:lock.flock(f,lock.LOCK_EX|lock.LOCK_NB)
 except BlockingIOError:sys.exit(42)
'''
            args=[sys.executable,'-c',code,str(Path(file_lock.__file__).parent),str(path)]
            with path.open('a') as f:
                file_lock.flock(f,file_lock.LOCK_EX|file_lock.LOCK_NB)
                self.assertEqual(subprocess.run(args,timeout=10).returncode,42)
                file_lock.flock(f,file_lock.LOCK_UN)
            self.assertEqual(subprocess.run(args,timeout=10).returncode,0)

    def test_changes_invalidate_confirmed_criteria(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            docs={
                'profile/identity.yaml':{'name':'Example','email':'user@example.invalid',
                    'location':'Test City','timezone':'UTC','cv_path':'profile/cv/resume.txt'},
                'config/preferences.yaml':{'roles':{'primary':['Software Developer']},
                    'scoring':{'auto_apply_threshold':55},'application':{'mode':'automatic'}},
                'config/targets.yaml':{'platforms':[{'name':'remotive','enabled':True}],
                    'run':{'trigger':'manual'}},'config/runtime.json':{'timezone':'UTC'}}
            for name,value in docs.items():setup_project.write_json(root/name,value)
            for name in ['profile/qa-bank.md','profile/cv/resume.txt','profile/cv/cv-extracted.md']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Synthetic evidence',encoding='utf-8')
            with patch.object(profile_tools,'ROOT',root):
                self.assertFalse(profile_tools.validate())
                self.assertTrue(profile_tools.validate(confirm=True))
                self.assertTrue(profile_tools.validate())
                docs['config/preferences.yaml']['scoring']['auto_apply_threshold']=65
                setup_project.write_json(root/'config/preferences.yaml',docs['config/preferences.yaml'])
                self.assertFalse(profile_tools.validate())

    def test_reinstall_preserves_existing_candidate_profile(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            shutil.copytree(setup_project.ROOT/'templates',root/'templates')
            identity=root/'profile/identity.yaml';identity.parent.mkdir()
            identity.write_text('name: Existing User',encoding='utf-8')
            setup_project.write_json(root/'data/browser-mcp/node_modules/@playwright/mcp/package.json',
                                     {'version':setup_project.BROWSER_VERSION})
            with patch.object(setup_project,'ROOT',root),patch.object(setup_project,'configure'),patch.object(setup_project.shutil,'which',return_value='npm'):
                setup_project.install()
            self.assertEqual(identity.read_text(encoding='utf-8'),'name: Existing User')
            self.assertTrue((root/'config/targets.yaml').exists())

    def test_export_excludes_private_state_and_generated_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in release.PUBLIC_FILES:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('public')
            for directory in release.PUBLIC_DIRS:(root/directory).mkdir(exist_ok=True)
            for name in ['config/runtime.json','profile/cv/private.pdf','secrets/token','.git/config','data/ledger.json']:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('PRIVATE')
            files=release.public_files(root)
            self.assertFalse(any('PRIVATE' in p.read_text() for p in files))

if __name__=='__main__':unittest.main()

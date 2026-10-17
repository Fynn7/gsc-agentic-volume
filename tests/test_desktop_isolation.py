"""Isolation contract tests. No Codex GUI/CLI or model is launched by these tests."""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import desktop_isolation as isolation
from windows_profile_probe import inspect,within
from isolation_guard import require_unchanged


class IsolatedDesktop(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows PowerShell 5.1 launch regression')
    def test_windows_powershell_file_entry_passes_python_preflight_with_spaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'launch folder with spaces';(root/'scripts').mkdir(parents=True)
            source=Path(__file__).resolve().parents[1]
            shutil.copy2(source/'Start-IsolatedVolumeDesktop.ps1',root/'Start-IsolatedVolumeDesktop.ps1')
            shutil.copy2(source/'scripts/check_desktop_python.py',root/'scripts/check_desktop_python.py')
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'Start-IsolatedVolumeDesktop.ps1'),'-Python',sys.executable,'-Verify'],capture_output=True,text=True,errors='replace',timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('No prepared experiment exists',result.stderr)
            self.assertNotIn('NameError',result.stderr)
            self.assertNotIn('Use an existing 64-bit',result.stderr)

    def config(self,root):
        run=root/'run';home=run/'desktop-home';home.mkdir(parents=True)
        return {'resultDirectory':str(run),'desktopHome':str(home),'model':'qwen3.6:35b'}

    def test_child_environment_is_private_and_does_not_mutate_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.config(root)
            inherited={'PATH':'keep','OPENAI_API_KEY':'unit-secret','CODEX_HOME':'daily','CODEX_ACCESS_TOKEN':'unit-token','SKY_CUA_NATIVE_PIPE_DIRECTORY':'daily','NODE_REPL_X':'daily'}
            env=isolation.launch_environment(config,inherited)
            self.assertEqual(inherited['CODEX_HOME'],'daily');self.assertEqual(env['PATH'],'keep')
            self.assertEqual(env['CODEX_HOME'],config['desktopHome'])
            self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('CODEX_ACCESS_TOKEN',env)
            self.assertNotIn('SKY_CUA_NATIVE_PIPE_DIRECTORY',env);self.assertNotIn('NODE_REPL_X',env)
            self.assertNotEqual(env['CODEX_HOME'],env['CODEX_ELECTRON_USER_DATA_PATH'])

    def test_daily_or_overlapping_directories_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.config(root)
            for home in [str(Path.home()/'.codex'),str(root/'run/electron-user-data'),str(root/'outside')]:
                with self.subTest(home=home),self.assertRaises(ValueError):isolation.launch_environment({**config,'desktopHome':home})

    def test_explicit_launch_command_is_built_without_explorer_and_baseline_is_not_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.config(root);exe=root/'ChatGPT.exe'
            child=Mock(pid=4321);child.poll.return_value=None
            with patch.object(isolation,'static_audit',return_value={'recognizedImplementation':True}),patch.object(isolation,'processes',return_value=[]),patch.object(isolation,'protected_snapshot',return_value={'files':{},'guiRoots':[],'processes':[]}),patch.object(isolation.time,'sleep'),patch.object(isolation.subprocess,'Popen',return_value=child) as spawn:
                isolation.launch(config,exe)
                command=spawn.call_args.args[0];env=spawn.call_args.kwargs['env']
                self.assertEqual(command,[str(exe),'--user-data-dir='+str(root/'run/electron-user-data')])
                self.assertEqual(env['CODEX_SQLITE_HOME'],config['desktopHome'])
                with self.assertRaisesRegex(ValueError,'already launched'):isolation.launch(config,exe)
                self.assertEqual(spawn.call_count,1)

    def test_protected_file_changes_are_reported_without_restoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'daily.toml';file.write_text('old')
            snapshot={'files':{str(file):isolation.digest(file)}}
            file.write_text('new')
            self.assertEqual(isolation.protected_changes(snapshot),[str(file)])
            self.assertEqual(file.read_text(),'new')

    def test_protected_file_guard_latches_and_preserves_user_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);file=root/'daily.toml';file.write_text('old')
            (root/'isolation-baseline.json').write_text(json.dumps({'files':{str(file):isolation.digest(file)}}))
            require_unchanged(root);file.write_text('new')
            with self.assertRaisesRegex(ValueError,'Protected daily'):require_unchanged(root)
            self.assertEqual(file.read_text(),'new');file.write_text('old')
            with self.assertRaisesRegex(ValueError,'latched'):require_unchanged(root)

    def test_environment_alone_cannot_pass_runtime_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.config(root);run=Path(config['resultDirectory']);home=Path(config['desktopHome'])
            catalog=run/'models.json';catalog.write_text(json.dumps({'models':[{'use_responses_lite':False}]}))
            (home/'config.toml').write_text('model_provider="volume_local"\nmodel_catalog_json='+json.dumps(str(catalog))+'\n')
            (run/'isolation-baseline.json').write_text(json.dumps({'files':{},'guiRoots':[],'processes':[]}))
            env=isolation.launch_environment(config)
            main={'pid':1234,'name':'ChatGPT.exe','role':'main-or-cli','environment':env}
            with patch.object(isolation,'profile_processes',return_value=([main],[])):
                report=isolation.verify(config)
            self.assertFalse(report['passed'])
            self.assertFalse(report['checks']['actualGuiFilesOpen'])
            self.assertFalse(report['checks']['actualHomeFilesOpen'])
            self.assertFalse(report['checks']['nativeAppServerObserved'])

    def test_protocol_requires_real_recorded_post_and_mcp_and_rejects_lite_or_leaked_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=self.config(root);run=Path(config['resultDirectory']);http=run/'http';http.mkdir()
            self.assertFalse(isolation.protocol(config)['passed'])
            metadata=http/'http-001.metadata.json';request=http/'http-001.request.bin'
            metadata.write_text(json.dumps({'method':'POST','path':'/v1/responses','status':200}))
            body={'model':config['model'],'input':[{'type':'message'}],'tools':[{'type':'namespace','name':'mcp__volume_renderer'}]}
            request.write_text(json.dumps(body));self.assertFalse(isolation.protocol(config)['passed'])
            calls=run/'mcp/connection';calls.mkdir(parents=True);(calls/'call-0001.request.json').write_text('{}')
            self.assertTrue(isolation.protocol(config)['passed'])
            request.write_text(json.dumps({**body,'input':[{'type':'additional_tools'}]}));self.assertFalse(isolation.protocol(config)['passed'])
            request.write_text(json.dumps({**body,'tools':[{'type':'namespace','name':'mcp__node_repl'}]}));self.assertFalse(isolation.protocol(config)['passed'])

    @unittest.skipUnless(os.name=='nt','Windows native read-only process inspection')
    def test_observer_reads_real_benign_child_environment_and_open_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);file=root/'Preferences';file.write_text('unit-only-profile')
            env=dict(os.environ,CODEX_HOME=str(root/'home'),CODEX_ELECTRON_USER_DATA_PATH=str(root/'gui'),GAV_DESKTOP_PROFILE_ID='unit-benign')
            child=subprocess.Popen([sys.executable,'-u','-c','import sys; f=open(sys.argv[1]); print("ready",flush=True); sys.stdin.readline()',str(file)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,env=env,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'ready')
                result=inspect(child.pid,handles=True)
                self.assertEqual(result['environment']['GAV_DESKTOP_PROFILE_ID'],'unit-benign')
                self.assertTrue(any(within(p,root) and p.endswith('Preferences') for p in result['openDiskPaths']))
            finally:
                child.stdin.write('\n');child.stdin.flush();child.wait(timeout=5)
                child.stdin.close();child.stdout.close()


if __name__=='__main__':unittest.main()

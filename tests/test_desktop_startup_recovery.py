"""Startup ownership boundaries. No Codex/GUI/model is launched or terminated."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import desktop_startup_recovery as recovery


class StartupRecovery(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows process identity and termination test')
    def test_creation_time_guard_and_owned_termination_on_benign_python_child(self):
        child=subprocess.Popen([sys.executable,'-u','-c','import sys; print(1,flush=True); sys.stdin.readline()'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'1')
            row=recovery.process_info(child.pid)
            with self.assertRaisesRegex(ValueError,'PID was reused'):recovery.terminate_verified({**row,'created':'2000-01-01T00:00:00+00:00'})
            self.assertIsNone(child.poll())
            recovery.terminate_verified(row);child.wait(timeout=5)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:child.terminate();child.wait(timeout=5)
            child.stdin.close();child.stdout.close()

    def config(self,root):
        run=root/'runtime/desktop/runs/unit';run.mkdir(parents=True)
        config={'formalHarness':'Codex Desktop','resultDirectory':str(run),'desktopHome':str(run/'desktop-home'),'python':sys.executable,'recordingProxy':'http://127.0.0.1:11436','rendererService':'http://127.0.0.1:8766'}
        (run/'config.json').write_text(json.dumps(config));return config,run

    def test_foreign_recording_service_is_never_stopped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,_=self.config(root)
            with patch.object(recovery,'ROOT',root),patch.object(recovery,'service_status',return_value={'resultDirectory':'foreign','formalHarness':'Codex Desktop'}),patch.object(recovery,'terminate_verified') as stop:
                with self.assertRaisesRegex(ValueError,'different/unknown'):recovery.recover(config)
                stop.assert_not_called()

    def test_inflight_model_request_prevents_cleanup_even_without_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,run=self.config(root);(run/'http').mkdir();(run/'http/http-001.request.bin').write_bytes(b'{"model":"qwen3.6:35b"}')
            with patch.object(recovery,'ROOT',root),patch.object(recovery,'service_status',return_value=None),patch.object(recovery,'profile_processes',return_value=([],[])),patch.object(recovery,'terminate_verified') as stop:
                with self.assertRaisesRegex(ValueError,'model/tool activity'):recovery.recover(config,True)
                stop.assert_not_called()

    def test_successful_unused_gui_is_reused_instead_of_killed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,run=self.config(root);(run/'desktop-launch.json').write_text('{}')
            rows=[{'pid':123,'name':'ChatGPT.exe','role':'main-or-cli'}]
            with patch.object(recovery,'ROOT',root),patch.object(recovery,'service_status',return_value=None),patch.object(recovery,'profile_processes',return_value=(rows,[])),patch.object(recovery,'terminate_verified') as stop:
                result=recovery.recover(config);self.assertEqual(result['action'],'reuse');stop.assert_not_called()

    def test_cleanup_of_exited_attempt_is_idempotent_and_keeps_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,run=self.config(root);log=run/'desktop-launch-failure.json';log.write_text('original failure')
            with patch.object(recovery,'ROOT',root),patch.object(recovery,'service_status',return_value=None),patch.object(recovery,'profile_processes',return_value=([],[])),patch.object(recovery,'terminate_verified') as stop:
                self.assertTrue(recovery.recover(config,True)['retryAllowed'])
                second=recovery.recover(config,True);self.assertEqual(second['action'],'already-closed');stop.assert_not_called()
            self.assertEqual(log.read_text(),'original failure');self.assertEqual(len((run/'startup-recovery-events.jsonl').read_text().splitlines()),1)

    def test_unrelated_pid_with_different_command_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,_=self.config(root)
            row={'pid':123,'executable':sys.executable,'command':'unrelated.py','created':'2026-10-17T00:00:00+00:00'}
            with patch.object(recovery,'ROOT',root),patch.object(recovery,'process_info',return_value=row):
                with self.assertRaisesRegex(ValueError,'ownership mismatch'):recovery.owned_python(123,config,'scripts/desktop_environment.py','unit/config.json')

    def test_daily_file_change_never_allows_retry_or_restores_user_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config,run=self.config(root);file=root/'daily.toml';file.write_text('user edit')
            (run/'isolation-baseline.json').write_text(json.dumps({'files':{str(file):'old-hash'},'processes':[]}))
            with patch.object(recovery,'ROOT',root),patch.object(recovery,'service_status',return_value=None),patch.object(recovery,'profile_processes',return_value=([],[])):
                result=recovery.recover(config,True);self.assertFalse(result['retryAllowed'])
            self.assertEqual(file.read_text(),'user edit')


if __name__=='__main__':unittest.main()

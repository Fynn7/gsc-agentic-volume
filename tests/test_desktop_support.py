"""Support-layer tests; synthetic inputs here are never model experiment data."""
import hashlib
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import desktop_environment as environment
from desktop_mcp import DesktopMcp
from collect_desktop_evidence import image_hashes


class DesktopSupport(unittest.TestCase):
    def test_config_patch_preserves_unrelated_tables_and_replaces_once(self):
        original='model="cloud"\n# retain comment\n[features]\nmemories=true\n[unrelated]\nvalue="keep"\n'
        result=environment.patch_toml(original,{'':{'model':'local','model_provider':'volume_local'},'features':{'memories':False},'mcp_servers.volume_renderer':{'enabled':True}})
        data=tomllib.loads(result)
        self.assertEqual(data['model'],'local');self.assertEqual(data['unrelated']['value'],'keep')
        self.assertFalse(data['features']['memories']);self.assertIn('# retain comment',result)
        self.assertEqual(environment.patch_toml(result,{'':{'model':'local'}}),result)

    def legacy_fixture(self, root):
        home=root/'config.toml';active=root/'active.json';run=root/'run';run.mkdir()
        original=b'# private user config\r\nmodel="original"\r\n[mcp_servers.node_repl.env]\r\nSKY_CUA_NATIVE_PIPE_DIRECTORY="old-pipe"\r\n'
        installed=b'model="local"\nmodel_provider="volume_local"\n'
        home.write_bytes(installed)
        (run/'user-config.original.toml').write_bytes(original)
        (run/'codex-desktop.config.toml').write_bytes(installed)
        environment.write_json(run/'preparation.json',{'originalConfigSha256':hashlib.sha256(original).hexdigest(),'installedSha256':hashlib.sha256(installed).hexdigest(),'installed':True,'originalExisted':True})
        environment.write_json(root/'installed.json',{'directory':str(run)})
        return home,active,run,original,installed

    def test_global_installer_is_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)/'config.toml';home.write_bytes(b'model="daily"\n')
            with patch.object(environment,'HOME_CONFIG',home),self.assertRaisesRegex(ValueError,'retired'):
                environment.install({})
            self.assertEqual(home.read_bytes(),b'model="daily"\n')

    def test_legacy_restore_bytes_and_refuse_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);home,active,run,original,installed=self.legacy_fixture(root)
            with patch.object(environment,'HOME_CONFIG',home),patch.object(environment,'ACTIVE',active):
                home.write_text('model="user-edit"\n')
                with self.assertRaisesRegex(ValueError,'changed'): environment.restore()
                self.assertTrue((root/'installed.json').exists())
                home.write_bytes(installed);environment.restore()
                self.assertEqual(home.read_bytes(),original)
                self.assertFalse(json.loads((run/'preparation.json').read_text())['installed'])
                environment.restore() # Idempotent, no pointer.

    def test_manual_restore_reconciles_only_known_pipe_without_config_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);home,active,run,original,_=self.legacy_fixture(root)
            current=original.replace(b'old-pipe',b'new-pipe');home.write_bytes(current)
            with patch.object(environment,'HOME_CONFIG',home),patch.object(environment,'ACTIVE',active):
                environment.restore()
            self.assertEqual(home.read_bytes(),current)
            self.assertFalse((root/'installed.json').exists())
            record=json.loads((run/'installation-closed.json').read_text())
            self.assertEqual(record['changedKeysFromOriginal'],list(environment.VOLATILE_CONFIG_KEYS))

    def test_other_mcp_user_changes_cannot_be_treated_as_manual_restoration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);home,active,run,original,_=self.legacy_fixture(root)
            home.write_bytes(original+b'UNRELATED_NEW_SETTING="preserve"\n');before=home.read_bytes()
            with patch.object(environment,'HOME_CONFIG',home),patch.object(environment,'ACTIVE',active):
                with self.assertRaisesRegex(ValueError,'changed'):environment.restore()
            self.assertEqual(home.read_bytes(),before);self.assertTrue((root/'installed.json').exists())

    def test_corrupted_backup_refuses_state_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);home,active,run,original,_=self.legacy_fixture(root)
            home.write_bytes(original);(run/'user-config.original.toml').write_text('model="tampered"\n')
            with patch.object(environment,'HOME_CONFIG',home),patch.object(environment,'ACTIVE',active),self.assertRaisesRegex(ValueError,'backup hash mismatch'):
                environment.restore()
            self.assertEqual(home.read_bytes(),original);self.assertTrue((root/'installed.json').exists())

    def test_prepare_isolated_candidate_disables_lite_and_does_not_copy_daily_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);user=root/'user';(user/'.codex').mkdir(parents=True)
            home=user/'.codex/config.toml';original=b'model="daily"\nnotify=["daily-only"]\n[mcp_servers.private]\ncommand="private"\n';home.write_bytes(original)
            environment.write_json(user/'.codex/models_cache.json',{'client_version':'unit-test','models':[{'slug':'gpt-template','use_responses_lite':True}]})
            volume=root/'unit.dat';volume.write_text('unit-preparation-only-never-rendered')
            template=root/'template.json';environment.write_json(template,{'formalHarness':'Codex Desktop','model':'qwen3.6:35b','endpoint':'http://127.0.0.1:11434','rendererService':'http://127.0.0.1:8766','recordingProxy':'http://127.0.0.1:11436','volumePath':str(volume),'contextSize':16384,'timeoutSeconds':300,'toolTimeoutSeconds':120})
            with patch.object(environment,'ROOT',root),patch.object(environment,'ACTIVE',root/'runtime/desktop/active.json'),patch.object(environment,'HOME_CONFIG',home),patch.object(Path,'home',return_value=user):
                result=environment.prepare(template)
            candidate=tomllib.loads((Path(result['desktopHome'])/'config.toml').read_text())
            self.assertEqual(set(candidate['mcp_servers']),{'volume_renderer'});self.assertNotIn('notify',candidate)
            models=json.loads((Path(result['resultDirectory'])/'models.json').read_text())
            self.assertIs(models['models'][0]['use_responses_lite'],False)
            self.assertEqual(home.read_bytes(),original);self.assertFalse((root/'runtime/desktop/installed.json').exists())

    def test_image_transport_hash_uses_bytes_not_path(self):
        import base64
        payload=b'unit-only-not-a-render'
        self.assertEqual(image_hashes({'x':'data:image/png;base64,'+base64.b64encode(payload).decode()}),[hashlib.sha256(payload).hexdigest()])
        self.assertEqual(image_hashes({'imageUrl':'C:/a/real.png'}),[])

    def test_mcp_errors_are_independently_journaled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=root/'config.json'
            config.write_text(json.dumps({'formalHarness':'Codex Desktop','rendererService':'http://127.0.0.1:1','resultDirectory':str(root/'results')}))
            controller=DesktopMcp(config)
            result=controller.call('render_scene',{})
            self.assertTrue(result['isError'])
            self.assertTrue((controller.directory/'call-0001.request.json').is_file())
            self.assertTrue((controller.directory/'call-0001.result.json').is_file())
            events=[json.loads(v) for v in (controller.directory/'events.jsonl').read_text().splitlines()]
            self.assertEqual(events[-1]['kind'],'tool_end');self.assertGreaterEqual(events[-1]['seconds'],0)

    def test_image_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);config=root/'config.json'
            config.write_text(json.dumps({'formalHarness':'Codex Desktop','rendererService':'http://127.0.0.1:1','resultDirectory':str(root/'results')}))
            controller=DesktopMcp(config)
            with patch.object(controller,'request',return_value=b'changed'),self.assertRaisesRegex(ValueError,'hash mismatch'):
                controller.image_result({'imageUrl':'/artifact/x','imageSha256':'0'*64})


if __name__=='__main__':unittest.main()

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

    def test_config_install_restore_bytes_and_refuse_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); home=root/'config.toml'; active=root/'active.json'; run=root/'run';run.mkdir()
            original=b'# private user config\r\nmodel="original"\r\n';home.write_bytes(original)
            (run/'codex-desktop.config.toml').write_text('model="local"\n')
            environment.write_json(run/'preparation.json',{'originalConfigSha256':environment.digest(home)})
            with patch.object(environment,'HOME_CONFIG',home),patch.object(environment,'ACTIVE',active):
                environment.install({'resultDirectory':str(run)})
                home.write_text('model="user-edit"\n')
                with self.assertRaisesRegex(ValueError,'changed'): environment.restore()
                home.write_bytes((run/'codex-desktop.config.toml').read_bytes());environment.restore()
                self.assertEqual(home.read_bytes(),original)

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

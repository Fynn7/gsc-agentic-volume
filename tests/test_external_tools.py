"""Boundary regressions: invalid external actions must not alter canonical state."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import app
from external_tools import validate_arguments, TOOLS


class ExternalBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = app.RUNTIME
        app.RUNTIME = Path(self.temp.name)
        self.session = app.Session('unused.dat', 'boundary check', 'qwen3.6:35b', 2)
        self.session.external = True; self.session.state = 'running'
        # Validation-only state: never used for rendering or experimental evidence.
        self.session.scene = {'revision': 4}
        self.tools = app.ExternalTools(app.Session, {self.session.id: self.session})

    def tearDown(self):
        app.RUNTIME = self.previous; self.temp.cleanup()

    def call(self, **args):
        return self.tools.call({'id':self.session.id,'name':'render_scene','arguments':args})

    def test_stale_revision_cannot_execute(self):
        before = copy.deepcopy(self.session.scene)
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.call(expectedRevision=3, reason='stale', parameters={'tfStart':0.2})
        self.assertEqual(self.session.scene, before)
        self.assertEqual(self.session.used, 0)
        self.assertFalse(any(self.session.directory.glob('frame-*')))

    def test_boolean_nan_and_unknown_fields_rejected(self):
        for parameters in [{'tfStart':True}, {'tfStart':float('nan')}, {'mask':'invented'}, {'rotationXYZ':[0,False,30]}]:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.call(expectedRevision=4, reason='invalid', parameters=parameters)
        self.assertEqual(self.session.used, 0)

    def test_budget_and_phase1_ownership(self):
        self.session.used = self.session.budget
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.call(expectedRevision=4, reason='too many', parameters={'tfStart':0.2})
        self.session.external = False
        with self.assertRaisesRegex(ValueError, 'Phase 1'):
            self.call(expectedRevision=4, reason='wrong owner', parameters={'tfStart':0.2})

    def test_slice_bounds_do_not_consume_budget(self):
        self.session.volume = {'dimensions':[7,5,3]}
        with self.assertRaisesRegex(ValueError, 'bounds'):
            self.tools.call({'id':self.session.id,'name':'inspect_slice','arguments':{'axis':'z','index':3}})
        self.assertEqual(self.session.used, 0)
        self.assertFalse(any(self.session.directory.glob('slice-*')))

    def test_empty_patch_and_concurrent_call_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Empty'):
            self.call(expectedRevision=4, reason='nothing', parameters={})
        self.session.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'busy'):
                self.call(expectedRevision=4, reason='concurrent', parameters={'tfStart':0.2})
        finally: self.session.lock.release()


class McpEncoding(unittest.TestCase):
    def test_redirected_stdio_remains_utf8_under_windows_codepage(self):
        request={'jsonrpc':'2.0','id':'image-\u00fc-\u00b0-\u56fe','method':'initialize','params':{}}
        environment=dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
        script=Path(__file__).resolve().parents[1]/'scripts/renderer_mcp.py'
        result=subprocess.run([sys.executable,'-B',str(script),'--service','http://127.0.0.1:1','--session','unused'],
                              input=(json.dumps(request,ensure_ascii=False)+'\n').encode('utf-8'),capture_output=True,env=environment,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        response=json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(response['id'],request['id'])
        self.assertIn('tools',response['result']['capabilities'])


if __name__ == '__main__': unittest.main()

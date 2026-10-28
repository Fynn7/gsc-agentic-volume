"""Preview volume import regressions: import snapshot, artifact URLs and session reuse."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import app


class VolumeImportSnapshot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = app.RUNTIME
        app.RUNTIME = Path(self.temp.name)
        app.VOLUME_IMPORTS.clear()

    def tearDown(self):
        app.RUNTIME = self.previous
        app.VOLUME_IMPORTS.clear()
        self.temp.cleanup()

    def fixture(self):
        candidate = Path(__file__).resolve().parents[1] / 'volumes' / 'present246x246x221.dat'
        self.assertTrue(candidate.is_file(), 'fixture volume missing')
        return str(candidate)

    def test_import_returns_snapshot_and_artifact_urls(self):
        record = app.create_volume_import(self.fixture())
        self.assertTrue(record['importId'])
        self.assertIn('volume', record)
        self.assertIn('dimensions', record['volume'])
        for key in ('datUrl', 'rawUrl'):
            self.assertTrue(record[key].startswith('/artifact/volume-imports/'))
            target = app.RUNTIME / record[key].removeprefix('/artifact/')
            self.assertTrue(target.is_file(), f'{key} target missing: {target}')

    def test_reload_survives_process_restart(self):
        record = app.create_volume_import(self.fixture())
        app.VOLUME_IMPORTS.clear()
        reloaded = app.get_volume_import(record['importId'])
        self.assertEqual(reloaded['importId'], record['importId'])
        self.assertEqual(reloaded['datUrl'], record['datUrl'])

    def test_unknown_import_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown volume import'):
            app.get_volume_import('does-not-exist')

    def test_session_reuses_snapshot_bytes(self):
        record = app.create_volume_import(self.fixture())
        session = app.Session(record['source'], 'reuse check', 'qwen3.5:9b')
        try:
            src_volume = Path(record['directory']) / 'volume'
            dst_volume = session.directory / 'volume'
            if dst_volume.exists():
                shutil.rmtree(dst_volume)
            shutil.copytree(src_volume, dst_volume)
            snapshot = json.loads((dst_volume / 'volume.json').read_text())
            self.assertEqual(snapshot['dimensions'], list(record['volume']['dimensions']))
            # Renderer payload bytes are identical, not reconverted.
            self.assertEqual(
                (dst_volume / 'render.raw').read_bytes(),
                (src_volume / 'render.raw').read_bytes(),
            )
        finally:
            shutil.rmtree(session.directory, ignore_errors=True)


class MaterializedBuiltinVolume(unittest.TestCase):
    """The Stage may only offer "run + preview" for a built-in that has a file."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = app.ROOT
        app.ROOT = Path(self.temp.name)
        (app.ROOT / 'volumes').mkdir()

    def tearDown(self):
        app.ROOT = self.previous
        self.temp.cleanup()

    def test_absent_file_means_preview_only(self):
        self.assertIsNone(app.materialized_builtin('head512'))

    def test_materialized_file_is_found(self):
        target = app.ROOT / 'volumes' / 'head512_8x8x8_uint8.dat'
        target.write_text('ObjectFileName: x.raw\nResolution: 8 8 8\n', encoding='utf-8')
        self.assertEqual(app.materialized_builtin('head512'), target)

    def test_stem_is_not_a_path(self):
        for stem in ('../secrets', '..\\secrets', 'head/512', '', None, 7):
            self.assertIsNone(app.materialized_builtin(stem), stem)


if __name__ == '__main__':
    unittest.main()

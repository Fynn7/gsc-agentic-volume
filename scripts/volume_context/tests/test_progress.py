import unittest
import io
import json
import tempfile
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from volume_context.progress import report, counted
from volume_context.hashing import sha256_file
import numpy as np

class ProgressTests(unittest.TestCase):
    def test_disabled_progress_produces_no_stdout_or_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict('os.environ', {'VOLUME_CONTEXT_PROGRESS':'0'}), redirect_stdout(out), redirect_stderr(err):
            report('reading', 'Read', force=True)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')

    def test_counted_passes_use_actual_voxels_and_start_independently(self):
        err = io.StringIO()
        chunks=[np.arange(3, dtype=np.uint16),np.arange(2,dtype=np.uint16)]
        with patch.dict('os.environ', {'VOLUME_CONTEXT_PROGRESS':'1'}), redirect_stderr(err):
            self.assertEqual(len(list(counted(chunks,'statistics','Stats',5,2))),2)
            self.assertEqual(len(list(counted(chunks,'conversion','Convert',5,2))),2)
        frames=[json.loads(line.removeprefix('@@volume-progress ')) for line in err.getvalue().splitlines()]
        for phase in ['statistics','conversion']:
            group=[frame for frame in frames if frame['phase']==phase]
            self.assertEqual(group[0]['completed'],0)
            self.assertEqual(group[-1]['completed'],5)
            self.assertEqual(group[-1]['unit'],'voxels')

    def test_hashing_reports_exact_bytes_without_changing_digest(self):
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/'input.raw';file.write_bytes(b'abc'*400000)
            expected=sha256_file(file);err=io.StringIO()
            with patch.dict('os.environ', {'VOLUME_CONTEXT_PROGRESS':'1'}), redirect_stderr(err):
                actual=sha256_file(file,'reading','Verify source')
            frames=[json.loads(line.removeprefix('@@volume-progress ')) for line in err.getvalue().splitlines()]
            self.assertEqual(actual,expected)
            self.assertEqual(frames[-1]['completed'],file.stat().st_size)
            self.assertEqual(frames[-1]['total'],file.stat().st_size)

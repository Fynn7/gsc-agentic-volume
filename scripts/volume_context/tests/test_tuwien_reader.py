"""Adapted binary-DAT regressions use numeric capability, not old Card protocol."""
import unittest,tempfile,struct,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'src'))
from volume import prepare
from volume_context.readers import read_source_header
from volume_context.budgets import Budgets
class TuWienTests(unittest.TestCase):
 def test_header_exclusion_and_axis_order(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder); values=np.arange(24,dtype='<u2'); p=root/'anonymous.dat'; p.write_bytes(struct.pack('<3H',2,3,4)+values.tobytes())
   result=prepare(p,root/'volume')
   self.assertEqual(result['dimensions'],(2,3,4)); self.assertEqual(Path(result['canonicalPath']).read_bytes(),values.tobytes()); self.assertIsNone(result['geometry']['spacing']); self.assertEqual(result['orientation'],'unknown')
 def test_large_input(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder); p=root/'anonymous.dat'; p.write_bytes(struct.pack('<3H',128,128,33)+bytes(128*128*33*2)); result=prepare(p,root/'volume'); self.assertEqual(result['dimensions'],(128,128,33))
 def test_truncated_extra_and_budget(self):
  for delta in [-2,2]:
   with self.subTest(delta=delta),tempfile.TemporaryDirectory() as folder:
    root=Path(folder); p=root/'anonymous.dat'; p.write_bytes(struct.pack('<3H',2,3,4)+bytes(48+delta))
    with self.assertRaises(Exception): prepare(p,root/'volume')
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder); p=root/'anonymous.dat'; p.write_bytes(struct.pack('<3H',2,3,4)+bytes(48)); h=read_source_header(p)
   with self.assertRaises(Exception): h.open_payload(root,Budgets(decoded_input_limit_bytes=8))
 def test_short_and_zero_dimensions(self):
  for data in [b'\x02\0',struct.pack('<3H',0,3,4)]:
   with tempfile.TemporaryDirectory() as folder:
    p=Path(folder)/'anonymous.dat'; p.write_bytes(data)
    with self.assertRaises(Exception): read_source_header(p)

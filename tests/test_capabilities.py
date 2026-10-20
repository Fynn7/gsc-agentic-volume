import unittest,tempfile,json,sys,copy
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from volume import prepare,inspect_slice
from renderer import EXE,initial_scene,validate_scene,render
class Capabilities(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
  self.values=np.arange(3*5*7,dtype='u1').reshape(3,5,7); self.values.tofile(self.root/'source.raw')
  (self.root/'source.dat').write_text('ObjectFileName: source.raw\nResolution: 7 5 3\nSliceThickness: 0.5 1 2\nFormat: uchar\nEndianess: little\n')
  self.volume=prepare(self.root/'source.dat',self.root/'imported')
 def tearDown(self): self.temp.cleanup()
 def test_slice_axes_and_spacing(self):
  self.assertEqual(list(self.volume['geometry']['spacing']),[.5,1,2])
  for axis,index,expected in [('x',2,self.values[:,:,2]),('y',3,self.values[:,3,:]),('z',1,self.values[1,:,:])]:
   r=inspect_slice(self.volume,axis,index,self.root); self.assertTrue(np.array_equal(np.array(Image.open(r['image'])),expected))
  with self.assertRaises(ValueError): inspect_slice(self.volume,'z',3,self.root)
 @unittest.skipUnless(EXE.exists(),'Raycaster.exe is not built (scripts/build-raycaster.ps1)')
 def test_invalid_action_preserves_scene(self):
  scene=initial_scene(self.volume); before=copy.deepcopy(scene)
  bad=copy.deepcopy(scene); bad['crop']=[0,8,0,5,0,3]
  with self.assertRaises(ValueError): validate_scene(bad,self.volume)
  self.assertEqual(scene,before)
  bad=copy.deepcopy(scene); bad['tf']['width']=0
  with self.assertRaises(ValueError): validate_scene(bad,self.volume)
  bad=copy.deepcopy(scene); bad['tf']['start']=True
  with self.assertRaises(ValueError): validate_scene(bad,self.volume)

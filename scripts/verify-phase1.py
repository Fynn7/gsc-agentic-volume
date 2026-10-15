"""Verify execution provenance, not semantic success or causal model attribution."""
import json,hashlib,base64,sys,ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def digest(data):return hashlib.sha256(data).hexdigest()
def load(p):return json.loads(p.read_text())
candidate=[]
for p in (ROOT/'runtime').glob('*/session.json'):
 s=load(p)
 if sum(e['kind']=='render' for e in s['events'])>=2:candidate.append((p,s))
assert candidate,'No session with two agent-driven renders'
p,s=candidate[-1]; directory=p.parent
frames=[e['result'] for e in s['events'] if e['kind'] in ['initial_render','render']]
for r in frames:
 assert digest(Path(r['image']).read_bytes())==r['imageSha256']
 assert load(Path(r['image']).with_name('scene.json'))==r['scene']
 assert load(Path(r['image']).with_name('native-state.json'))==r['nativeState']
 assert load(Path(r['image']).with_name('process.json'))['exitCode']==0
requests=[]
for i in [1,2]:
 reqpath=directory/f'call-{i}'/'request.json'; raw=reqpath.read_bytes(); req=json.loads(raw)
 assert digest(raw)==reqpath.with_name('request.sha256').read_text()
 expected=frames[i-1]; actual=base64.b64decode(req['messages'][-1]['images'][0]); assert digest(actual)==expected['imageSha256']
 lines=[json.loads(line) for line in reqpath.with_name('response.ndjson').read_text().splitlines()]
 assert lines[-1]['done'] and lines[-1].get('done_reason')!='length'
 action=json.loads(reqpath.with_name('content.txt').read_text()); assert action['tool']=='render_scene'; assert action['sceneRevision']==frames[i-1]['scene']['revision']
 assert action['parameters']['rotationXYZ']==frames[i]['scene']['camera']['rotationXYZ']
 requests.append({'call':i,'inputFrameHash':expected['imageSha256'],'newFrameHash':frames[i]['imageSha256'],'selectedRotationXYZ':action['parameters']['rotationXYZ']})
for source in list((ROOT/'src').glob('*.py'))+list((ROOT/'scripts').glob('*.py')):ast.parse(source.read_text())
report={'phase':1,'status':'PASS','session':s['id'],'stateAtVerification':s['state'],'actualFrames':len(frames),'twoDependentDecisions':requests,'model':s['model'],'actualModel':load(directory/'model.json')['installed'],'screenshotGui':'audit/phase1-gui.jpg','scope':'Actual images delivered to both real model calls and resulting native operations verified. This does not prove causal necessity of visual feedback, target visibility, identity, task success, generalization or superiority.','preservedFailures':['ed2ebf68-2474-4925-9d9d-2a4119d85489','a360e2a7-4e00-42e1-a71f-e5bcd537c97f']}
(ROOT/'audit/phase1-gate.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report))

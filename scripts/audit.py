"""Read-only baseline inventory and integrity verification; outputs only here."""
import json,hashlib,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OLD=Path(r'D:\fynns_local_ws\gsc-live-preview')
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def git(*args):return subprocess.check_output(['git','-C',str(OLD),*args],text=True).strip()
frozen=json.loads((ROOT/'audit/baseline-state.json').read_text())
current={'head':git('rev-parse','HEAD'),'statusPorcelain':git('status','--porcelain=v1')}
assert current['head']==frozen['head'] and current['statusPorcelain']==frozen['statusPorcelain'],'Baseline changed'
hist=OLD/'tools/gsc-live-preview/tmp/prompt-only-20261014'
entries=[]
for p in hist.rglob('*'):
 if p.is_file() and p.suffix.lower() in ['.json','.jsonl','.txt','.md','.png','.csv','.mjs','.ts','.sha256']:
  entries.append({'source':str(p),'bytes':p.stat().st_size,'sha256':sha(p)})
(ROOT/'audit/historical-artifact-hashes.json').write_text(json.dumps(entries,indent=2))
summary=json.loads((hist/'experiment-summary.json').read_text()); proposals=json.loads((hist/'all-display-proposals.json').read_text()); calls=json.loads((hist/'all-call-results.json').read_text())
report={'baselineUnchanged':True,'current':current,'frozenArtifactCount':len(entries),'callRecords':len(calls),'proposals':len(proposals),'blackProposals':sum(p.get('pixels')==0 for p in proposals),'summary':summary,'note':'Record/hash verification; no semantic re-scoring or baseline reproduction'}
(ROOT/'audit/verification.json').write_text(json.dumps(report,indent=2)); print(json.dumps({k:v for k,v in report.items() if k!='summary'}))

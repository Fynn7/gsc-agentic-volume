"""Read-only configuration/recorded-wire audit. Never launches an agent or inference."""
import argparse
import collections
import hashlib
import json
import tomllib
from pathlib import Path
from desktop_environment import changed_config_keys, flatten


def describe(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'mtimeUnix':path.stat().st_mtime}


def audit(run, current):
    original=run/'user-config.original.toml';installed=run/'codex-desktop.config.toml'
    files={'original':original,'installed':installed,'current':current}
    before=list(current.parent.glob('config.toml.before-manual-restore-*'))
    if before:files['beforeManualRestore']=max(before,key=lambda p:p.stat().st_mtime)
    report={'scope':'read-only-incident-audit-NOT-model-experiment','run':str(run),'files':{k:describe(p) for k,p in files.items()},'diffs':{}}
    for a,b in [('original','installed'),('installed','current'),('original','current')]+([('installed','beforeManualRestore')] if before else []):
        report['diffs'][a+' -> '+b]=changed_config_keys(files[a].read_bytes(),files[b].read_bytes())
    report['preparation']=json.loads((run/'preparation.json').read_text())
    report['catalog']={k:json.loads((run/'models.json').read_text())['models'][0].get(k) for k in ['slug','use_responses_lite','tool_mode','context_window']}
    report['ollamaVersion']=json.loads((run/'model.json').read_text())['version']
    report['desktopPackage']=json.loads((run/'desktop-package.json').read_text())
    report['requests']=[]
    for path in sorted((run/'http').glob('*.metadata.json')):
        meta=json.loads(path.read_text());prefix=path.name.replace('.metadata.json','')
        entry={'metadata':describe(path),**meta}
        if meta['method']=='POST':
            request_path=path.with_name(prefix+'.request.bin');forwarded_path=path.with_name(prefix+'.forwarded.bin')
            incoming=json.loads(request_path.read_bytes());outgoing=json.loads(forwarded_path.read_bytes())
            items=incoming.get('input',[])
            entry.update(request=describe(request_path),forwarded=describe(forwarded_path),model=incoming.get('model'),
                         inputItemTypes=dict(collections.Counter(v.get('type','message') for v in items if isinstance(v,dict))),
                         topLevelTools=len(incoming.get('tools') or []),inputUnchangedByProxy=items==outgoing.get('input'))
            for item in items:
                if isinstance(item,dict) and item.get('type')=='additional_tools':
                    entry['additionalTools']={'types':dict(collections.Counter(t.get('type') for t in item.get('tools',[]))),
                                              'names':[t.get('name') for t in item.get('tools',[])],
                                              'sha256':hashlib.sha256(json.dumps(item,sort_keys=True).encode()).hexdigest()}
        response=path.with_name(prefix+'.response.bin')
        if response.exists():
            entry['response']=describe(response)
            if meta.get('status',200)>=400:entry['errorText']=response.read_text(encoding='utf-8',errors='replace')[:800]
        report['requests'].append(entry)
    report['mcpToolCallFiles']=[str(p) for p in (run/'mcp').glob('*/call-*.request.json')]
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);parser.add_argument('--output');args=parser.parse_args()
    result=audit(Path(args.run).resolve(),Path.home()/'.codex/config.toml')
    raw=json.dumps(result,indent=2)
    if args.output:
        path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(raw,encoding='utf-8')
        print(json.dumps({'report':str(path),'currentVsOriginalChangedKeys':result['diffs']['original -> current'],'catalog':result['catalog'],'recordedPostStatuses':[r.get('status') for r in result['requests'] if r['method']=='POST'],'mcpToolCalls':len(result['mcpToolCallFiles'])},indent=2))
    else:print(raw)

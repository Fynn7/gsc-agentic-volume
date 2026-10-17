"""Summarize recorded Desktop transport; never promotes claims to visual success."""
import argparse
import base64
import hashlib
import html
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def image_hashes(value):
    found=[]
    if isinstance(value,dict):
        if value.get('type')=='image' and value.get('mimeType')=='image/png' and 'data' in value:
            found.append(hashlib.sha256(base64.b64decode(value['data'],validate=True)).hexdigest())
        for child in value.values(): found.extend(image_hashes(child))
    elif isinstance(value,list):
        for child in value: found.extend(image_hashes(child))
    elif isinstance(value,str) and value.startswith('data:image/') and ';base64,' in value:
        found.append(hashlib.sha256(base64.b64decode(value.split(';base64,',1)[1],validate=True)).hexdigest())
    return found


def collect(directory):
    config=json.loads((directory/'config.json').read_text(encoding='utf-8-sig'))
    mcp=[]; returned=set(); errors=[]
    for path in sorted((directory/'mcp').glob('*/call-*.result.json')):
        value=json.loads(path.read_text()); hashes=image_hashes(value);returned.update(hashes)
        req=json.loads(path.with_name(path.name.replace('.result.','.request.')).read_text())
        item={'file':str(path),'name':req['name'],'arguments':req['arguments'],'isError':value.get('isError'),'imageHashes':hashes}
        mcp.append(item)
        if item['isError']: errors.append(item)
    requests=[]; received=set()
    for path in sorted((directory/'http').glob('*.metadata.json')):
        meta=json.loads(path.read_text())
        if meta['method']!='POST' or meta['path'] not in ('/v1/responses','/v1/chat/completions','/api/chat'): continue
        body_path=path.with_name(path.name.replace('.metadata.json','.forwarded.bin'))
        body=json.loads(body_path.read_bytes()) if body_path.exists() else {}
        hashes=image_hashes(body);received.update(hashes)
        response_path=path.with_name(path.name.replace('.metadata.json','.response.bin'))
        events=[]
        if response_path.exists():
            for line in response_path.read_text(encoding='utf-8',errors='replace').splitlines():
                if line.startswith('data: '):
                    try: events.append(json.loads(line[6:]))
                    except json.JSONDecodeError: pass
            if not events:
                try: events=[json.loads(response_path.read_bytes())]
                except (json.JSONDecodeError,UnicodeDecodeError): pass
        usages=[v.get('response',v).get('usage') for v in events if isinstance(v,dict) and v.get('response',v).get('usage')]
        requests.append({**meta,'model':body.get('model'),'imageHashes':hashes,'matchingMcpPngHashes':sorted(set(hashes)&returned),'usage':usages})
    links=[]; pictures=[]
    sessions_file=directory/'sessions.jsonl'
    if sessions_file.exists():
        for line in sessions_file.read_text().splitlines():
            link=json.loads(line); service=Path(link['serviceDirectory']);links.append(link)
            for frame in sorted(service.glob('frame-*'),key=lambda p:int(p.name.split('-')[-1])):
                pictures.append({'sessionId':link['sessionId'],'image':str(frame/'image.png'),'scene':str(frame/'scene.json'),'nativeState':str(frame/'native-state.json')})
    report={'formalHarness':'Codex Desktop','configuredModel':config['model'],'endpoint':config['endpoint'],'scope':config.get('scope'),
            'desktopGuiAcceptance':'pending human screenshots and inspection','visualTaskSuccess':'unverified; inspect actual target visibility independently',
            'mcpCalls':mcp,'errors':errors,'inferenceCalls':requests,'pngHashesReturnedByMcp':sorted(returned),
            'pngHashesInInferenceInput':sorted(received),'exactPngTransportMatches':sorted(returned&received),
            'sessions':links,'frames':pictures,'telemetry':str(directory/'telemetry.jsonl')}
    report['generationRequestAttempts']=len(requests)
    report['successfulGenerationResponses']=sum(r.get('status')==200 for r in requests)
    report['blockedBeforeForwarding']=sum(bool(r.get('error')) for r in requests)
    if requests:
        report['inferenceWallSeconds']=max(r.get('ended',r['started']) for r in requests)-min(r['started'] for r in requests)
        report['sumInferenceRequestSeconds']=sum(r.get('seconds',0) for r in requests)
    telemetry_path=directory/'telemetry.jsonl'
    if telemetry_path.exists():
        context_values=set();vram_values=set()
        for line in telemetry_path.read_text().splitlines():
            try:
                sample=json.loads(line)
                for model in sample.get('ollama',{}).get('models',[]):
                    if model.get('name')==config['model'] or model.get('model')==config['model']:
                        if model.get('context_length') is not None:context_values.add(model['context_length'])
                        if model.get('size_vram') is not None:vram_values.add(model['size_vram'])
            except (ValueError,TypeError):pass
        report['observedOllamaContextSizes']=sorted(context_values)
        report['configuredContextSize']=config['contextSize']
        report['contextMismatch']=any(v!=config['contextSize'] for v in context_values)
        report['observedOllamaVramBytes']=sorted(vram_values)
    (directory/'evidence.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['<!doctype html><meta charset="utf-8"><title>Recorded Desktop evidence</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto}img{max-width:640px}pre{white-space:pre-wrap}</style>',
           '<h1>Recorded transport and native frames</h1><p>GUI acceptance and visual task success require human review. Interface tests are not model experiments.</p>',
           '<p>Scope: '+html.escape(str(config.get('scope')))+'; generation request attempts: '+str(len(requests))+'; successful responses: '+str(report['successfulGenerationResponses'])+'; blocked before forwarding: '+str(report['blockedBeforeForwarding'])+'; MCP calls: '+str(len(mcp))+'; exact PNG transport matches: '+str(len(returned&received))+'</p>']
    for frame in pictures:
        lines.append('<figure><img src="'+html.escape(Path(frame['image']).as_uri())+'"><figcaption>'+html.escape(frame['sessionId']+' / '+Path(frame['image']).parent.name)+' — <a href="'+html.escape(Path(frame['scene']).as_uri())+'">Scene JSON</a></figcaption></figure>')
    lines.append('<pre>'+html.escape(json.dumps({'calls':mcp,'inference':requests,'errors':errors},indent=2))+'</pre>')
    (directory/'evidence.html').write_text('\n'.join(lines),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run');args=parser.parse_args()
    directory=Path(args.run) if args.run else Path((ROOT/'runtime/desktop/LATEST.txt').read_text().strip())
    report=collect(directory)
    print(json.dumps({'report':str(directory/'evidence.html'),'scope':report['scope'],'generationRequestAttempts':report['generationRequestAttempts'],'successfulGenerationResponses':report['successfulGenerationResponses'],'blockedBeforeForwarding':report['blockedBeforeForwarding'],'mcpCalls':len(report['mcpCalls']),'exactPngTransportMatches':len(report['exactPngTransportMatches']),'desktopGuiAcceptance':report['desktopGuiAcceptance']},indent=2))

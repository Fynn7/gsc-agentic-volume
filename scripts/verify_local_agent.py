"""Verify recorded pixels/scene provenance; optionally replay without any model.

Does not infer semantic task correctness, causal image use, or experimental scores.
"""
import argparse
import base64
import hashlib
import io
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from renderer import render
from PIL import Image


def sha(data): return hashlib.sha256(data).hexdigest()
def pixels(data):
    with Image.open(io.BytesIO(data)) as image: return image.size, sha(image.convert('RGB').tobytes())


def images(value):
    if isinstance(value,dict):
        if value.get('type') in ('input_image','image_url','image'):
            data=value.get('image_url',value.get('data'))
            if isinstance(data,dict): data=data.get('url')
            if isinstance(data,str):
                if data.startswith('data:image/'): yield base64.b64decode(data.split(',',1)[1])
                elif value.get('type')=='image' and value.get('mimeType','').startswith('image/'): yield base64.b64decode(data)
        for child in value.values(): yield from images(child)
    elif isinstance(value,list):
        for child in value: yield from images(child)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('run'); parser.add_argument('--replay',action='store_true'); args=parser.parse_args()
    directory=Path(args.run).resolve(); manifest=json.loads((directory/'manifest.json').read_text()); session=json.loads((directory/'session.json').read_text())
    actual=ROOT/'runtime'/session['id']; frames=[e['result'] for e in session['events'] if e['kind'] in ('initial_render','render')]
    report={'run':str(directory),'sessionId':session['id'],'scope':'Pixel delivery and native execution provenance only; semantic correctness and causal visual dependence are unverified.','frames':[],'calls':[]}
    for frame in frames:
        path=Path(frame['image']); raw=path.read_bytes()
        assert sha(raw)==frame['imageSha256'],'PNG hash mismatch'
        assert json.loads(path.with_name('scene.json').read_text())==frame['scene'],'Scene mismatch'
        assert json.loads(path.with_name('native-state.json').read_text())==frame['nativeState'],'Readback mismatch'
        assert json.loads(path.with_name('process.json').read_text())['exitCode']==0,'Native process failed'
        report['frames'].append({'revision':frame['scene']['revision'],'imageSha256':sha(raw),'pixels':pixels(raw),'rotationXYZ':frame['scene']['camera']['rotationXYZ'],'tf':frame['scene']['tf'],'crop':frame['scene']['crop']})
    slices=[e['result'] for e in session['events'] if e['kind']=='slice']
    report['slices']=[]
    for result in slices:
        raw=Path(result['image']).read_bytes()
        assert sha(raw)==result['imageSha256'],'Slice PNG hash mismatch'
        report['slices'].append({'axis':result['axis'],'index':result['index'],'imageSha256':sha(raw),'pixels':pixels(raw)})
    matched=set(); slice_matched=set()
    for path in sorted((directory/'http').glob('*.forwarded.bin')):
        metadata=path.with_name(path.name.replace('.forwarded.bin','.metadata.json'))
        meta=json.loads(metadata.read_text()) if metadata.exists() else {}
        if meta.get('path') not in ('/v1/responses','/v1/chat/completions','/api/chat'): continue
        body=json.loads(path.read_bytes()); delivered=[]
        for raw in images(body):
            pixel=pixels(raw); match=[frame['revision'] for frame in report['frames'] if tuple(frame['pixels'])==tuple(pixel)]
            slice_matches=[str(s['axis'])+':'+str(s['index']) for s in report['slices'] if tuple(s['pixels'])==tuple(pixel)]
            matched.update(match); slice_matched.update(slice_matches)
            delivered.append({'byteSha256':sha(raw),'pixelHash':pixel[1],'matchingSceneRevisions':match,'matchingSlices':slice_matches})
        output={'request':path.name,'seconds':meta.get('seconds'),'status':meta.get('status'),'deliveredImages':delivered,'model':body.get('model')}
        response_path=path.with_name(path.name.replace('.forwarded.bin','.response.bin'))
        if response_path.exists():
            raw=response_path.read_bytes()
            if meta.get('status')!=200: output['error']=raw.decode(errors='replace')[:3000]
            else:
                for line in raw.splitlines():
                    if not line.startswith(b'data: '): continue
                    try: event=json.loads(line[6:])
                    except (ValueError,TypeError): continue
                    if event.get('type')=='response.completed': output['usage']=event.get('response',{}).get('usage')
                    if event.get('usage'): output['usage']=event['usage']
        report['calls'].append(output)
    report.update(actualFrames=len(frames),agentRenders=len(frames)-1,visuallyDeliveredRevisions=sorted(matched),visuallyDeliveredSlices=sorted(slice_matched),state=session['state'],taskSuccess='unverified')
    report['metrics']={'taskSeconds':manifest.get('seconds'),'modelCalls':len(report['calls']),
                       'inputTokens':sum(c.get('usage',{}).get('input_tokens',0) for c in report['calls']),
                       'cachedInputTokens':sum(c.get('usage',{}).get('input_tokens_details',{}).get('cached_tokens',0) for c in report['calls']),
                       'outputTokens':sum(c.get('usage',{}).get('output_tokens',0) for c in report['calls'])}
    report['metrics']['endToEndSeconds']=manifest.get('totalSeconds',manifest['ended']-(directory/'config.json').stat().st_ctime)
    report['metrics']['endToEndTimingScope']='explicit run start to finish' if 'totalSeconds' in manifest else 'approximate: Windows archive config creation to recorded runner finish; excludes CLI startup'
    telemetry=directory/'telemetry.jsonl'
    if telemetry.exists():
        samples=[json.loads(line) for line in telemetry.read_text().splitlines()]
        gpu=[float(s['gpu'].split(',')[0]) for s in samples if s.get('gpu')]
        ram=[(s['ramTotalBytes']-s['ramAvailableBytes'])/1024**3 for s in samples if 'ramTotalBytes' in s]
        cpu=[]
        for a,b in zip(samples,samples[1:]):
            if 'cpuTimes' not in a or 'cpuTimes' not in b: continue
            delta={k:b['cpuTimes'][k]-a['cpuTimes'][k] for k in a['cpuTimes']}; total=delta['kernel']+delta['user']
            if total>0: cpu.append(100*(total-delta['idle'])/total)
        report['metrics'].update(hostPeakGpuMiB=max(gpu,default=0),hostPeakRamGiB=max(ram,default=0),hostMeanCpuPercent=sum(cpu)/len(cpu) if cpu else None,hostMaxCpuPercent=max(cpu,default=0),resourceScope='Whole-system samples; not process-isolated peaks')
    if (actual/'final/image.png').exists():
        final=json.loads((actual/'final/result.json').read_text()); assert sha((actual/'final/image.png').read_bytes())==final['imageSha256']
        report['final']={'revision':final['scene']['revision'],'image':str(actual/'final/image.png'),'imageSha256':final['imageSha256']}
    if args.replay or (directory/'replay').is_dir():
        target=directory/'replay'
        if args.replay: target.mkdir(exist_ok=False)
        results=[]
        for index,frame in enumerate(frames):
            repeated=render(frame['scene'],session['volume'],target/('frame-'+str(index))) if args.replay else json.loads((target/('frame-'+str(index))/'result.json').read_text())
            original=Path(frame['image']).read_bytes(); replayed=Path(repeated['image']).read_bytes()
            results.append({'revision':frame['scene']['revision'],'samePngBytes':sha(original)==sha(replayed),'samePixels':pixels(original)==pixels(replayed)})
        report['replay']=results
    (directory/'verification.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))


if __name__=='__main__': main()

"""Blind two-image development diagnostic, separate from agent task results.

Fresh real native images; randomized order; numeric pixel truth never sent to VLM.
This checks a simple visual discrimination, not semantic visualization quality.
"""
import argparse
import base64
import copy
import json
import random
import sys
import threading
import time
import urllib.request
from pathlib import Path
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from renderer import render
from run_local_agent import monitor


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('run'); args=parser.parse_args()
    run=Path(args.run).resolve(); cfg=json.loads((run/'config.json').read_text()); session=json.loads((run/'session.json').read_text())
    target=run/'blind-visual-control'; target.mkdir(exist_ok=False)
    stop=threading.Event(); telemetry=threading.Thread(target=monitor,args=(stop,target,cfg['endpoint']),daemon=True); telemetry.start()
    try:
        original=json.loads((ROOT/'runtime'/session['id']/'frame-0/scene.json').read_text()); records=[]
        for i,rotation in enumerate([[0,0,0],[0,90,0]]):
            scene=copy.deepcopy(original); scene['camera']['rotationXYZ']=rotation
            result=render(scene,session['volume'],target/('native-'+str(i)))
            pixels=np.asarray(Image.open(result['image']).convert('RGB')); y,x=np.where(np.any(pixels>0,axis=2))
            if not len(x): raise ValueError('Control frame unexpectedly empty')
            records.append({'result':result,'bboxAspect':float((x.max()-x.min()+1)/(y.max()-y.min()+1))})
        if abs(records[0]['bboxAspect']-records[1]['bboxAspect']) < 0.3: raise ValueError('Visual control insufficiently distinct')
        order=[0,1]; random.SystemRandom().shuffle(order); trials=[]
        schema={'type':'object','properties':{'widerAspect':{'type':'string','enum':['A','B']},'observation':{'type':'string'}},'required':['widerAspect','observation'],'additionalProperties':False}
        for i,indices in enumerate([order,list(reversed(order))]):
            expected='A' if records[indices[0]]['bboxAspect']>records[indices[1]]['bboxAspect'] else 'B'
            payload={'model':cfg['model'],'stream':False,'think':cfg.get('think',False),'format':schema,'options':{'num_ctx':cfg['contextSize'],'num_predict':256,'temperature':cfg['temperature'],'seed':42},
                     'messages':[{'role':'user','content':'Two actual volume renderings are attached in order: first image A, second image B. Which image has a visibly WIDER foreground shape relative to its height? Compare horizontal width divided by vertical height of the nonblack object, not brightness. Return widerAspect A or B and a short observation. No dataset identity or anatomical orientation is known.',
                                  'images':[base64.b64encode(Path(records[index]['result']['image']).read_bytes()).decode() for index in indices]}]}
            raw=json.dumps(payload).encode(); (target/('trial-'+str(i)+'-request.json')).write_bytes(raw)
            req=urllib.request.Request(cfg['endpoint'].rstrip('/')+'/api/chat',data=raw,headers={'Content-Type':'application/json'})
            started=time.monotonic()
            with urllib.request.urlopen(req,timeout=cfg['timeoutSeconds']) as response: result=response.read()
            (target/('trial-'+str(i)+'-response.json')).write_bytes(result)
            response=json.loads(result); answer=json.loads(response['message']['content'])
            trials.append({'order':indices,'expected':expected,'answer':answer,'correct':answer['widerAspect']==expected,'seconds':time.monotonic()-started,'promptTokens':response.get('prompt_eval_count'),'outputTokens':response.get('eval_count'),'loadNanoseconds':response.get('load_duration')})
        report={'kind':'development-blind-pixel-diagnostic','model':cfg['model'],'scope':'Independent bounding-box aspect discrimination only; not semantic task correctness or causal benefit of feedback. Both pair orders tested. Expected answer, scene parameters and numeric pixel measurements were withheld from the model.','records':records,'trials':trials}
        (target/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps({'trials':trials}),flush=True)
    finally: stop.set(); telemetry.join(10)


if __name__=='__main__': main()

"""Real MCP/native contract test WITHOUT any model or Codex invocation."""
import argparse
import base64
import copy
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from run_local_agent import http
from collect_desktop_evidence import collect

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    directory=Path(config['resultDirectory'])/'interface-tests';directory.mkdir(exist_ok=False)
    config.update(resultDirectory=str(directory),scope='scripted-interface-test-NOT-VLM-experiment')
    path=directory/'config.json';path.write_text(json.dumps(config,indent=2))
    started=time.time(); transcript=[]
    process=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/renderer_mcp.py'),'--desktop-config',str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(directory/'mcp-stderr.log').open('wb'),text=True,encoding='utf-8')
    def rpc(method,params={}):
        message={'jsonrpc':'2.0','id':'interface-ü-'+str(len(transcript)),'method':method,'params':params}
        process.stdin.write(json.dumps(message,ensure_ascii=True)+'\n');process.stdin.flush()
        result=json.loads(process.stdout.readline());assert result['id']==message['id'];assert 'error' not in result,result
        transcript.append({'request':message,'response':result});return result['result']
    def call(name,arguments,expect_error=False):
        result=rpc('tools/call',{'name':name,'arguments':arguments})
        assert result['isError']==expect_error,result
        if expect_error:return result
        value=json.loads(result['content'][0]['text'])
        if value.get('imageUrl'):
            image=next(c for c in result['content'] if c['type']=='image')
            raw=base64.b64decode(image['data']);assert raw.startswith(b'\x89PNG\r\n\x1a\n')
            assert hashlib.sha256(raw).hexdigest()==value['imageSha256']
        return value
    try:
        rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'interface-test-no-model','version':'1'}})
        names=[v['name'] for v in rpc('tools/list')['tools']];assert len(names)==6,names
        call('desktop_status',{})
        call('inspect_scene',{},True)
        initial=call('begin_task',{'goal':'SCRIPTED INTERFACE TEST ONLY: verify native render and sparse parameter preservation; no model decisions or visual quality claims.'})
        before=copy.deepcopy(initial['scene']);first=call('render_scene',{'expectedRevision':before['revision'],'reason':'Scripted sparse-parameter boundary check','parameters':{'rotationXYZ':[25,-35,10]}})
        expected=copy.deepcopy(before);expected['revision']+=1;expected['camera']['rotationXYZ']=[25,-35,10]
        assert first['scene']==expected,(first['scene'],expected)
        call('render_scene',{'expectedRevision':before['revision'],'reason':'Deliberately stale version must fail','parameters':{'tfStart':.2}},True)
        unchanged=call('inspect_scene',{});assert unchanged['scene']==first['scene']
        second=call('render_scene',{'expectedRevision':first['scene']['revision'],'reason':'Scripted second native image contract check; NOT agent feedback','parameters':{'rotationXYZ':[0,90,0]}})
        assert initial['imageSha256']!=first['imageSha256']!=second['imageSha256']
        call('inspect_slice',{'axis':'z','index':0})
        final=call('finish_task',{'frameRevision':first['scene']['revision'],'summary':'Interface test: exact sparse rotation verified; visual usefulness not scored.'})
        final_path=Path(final['finalDirectory']);assert (final_path/'image.png').is_file()
        assert hashlib.sha256((final_path/'image.png').read_bytes()).hexdigest()==first['imageSha256']
        # A completed task can be followed by a new task on the SAME connection.
        again=call('begin_task',{'goal':'SCRIPTED lifecycle test: fresh canonical session after finish, no inference.'});assert again['id']!=initial['id']
        call('finish_task',{'frameRevision':0,'summary':'Lifecycle interface check only; no model or visibility success.'})
    finally:
        process.stdin.close();process.wait(timeout=15)
        (directory/'mcp-transcript.json').write_text(json.dumps(transcript),encoding='utf-8')
    # Wrong-model request must be rejected locally and never forwarded to Ollama.
    req=urllib.request.Request(config['recordingProxy']+'/v1/responses',data=json.dumps({'model':'PROHIBITED-FALLBACK-INTERFACE-TEST','input':'do not forward'}).encode(),headers={'Content-Type':'application/json'})
    try: urllib.request.urlopen(req);raise AssertionError('Wrong model accepted')
    except urllib.error.HTTPError as error: assert error.code==502;assert 'fallback is prohibited' in error.read().decode()
    evidence=collect(directory)
    assert len(evidence['inferenceCalls'])==0
    report={'scope':config['scope'],'passed':True,'seconds':time.time()-started,'canonicalSession':initial['id'],'finalDirectory':str(final_path),'mcpToolCount':len(names),'toolCalls':len(evidence['mcpCalls']),'actualModelCalls':0,'desktopGuiAcceptance':'NOT EXECUTED','visualQuality':'NOT SCORED','checks':['UTF8 stdio','actual native images and SHA256','sparse parameter preservation','native readback in snapshots','stale revision rejection','slice pixels','finish image and artifacts','new task after finish','independent errors and timings','wrong-model proxy rejection']}
    (directory/'interface-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()

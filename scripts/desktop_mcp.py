"""Desktop MCP lifecycle and independent journal; delegates all rendering.

No model client or agent loop. Every Desktop connection has its own session.
"""
import base64
import hashlib
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def object_schema(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


class DesktopMcp:
    def __init__(self, config_path):
        self.config_path=Path(config_path).resolve()
        self.config=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        if self.config.get('formalHarness')!='Codex Desktop': raise ValueError('Formal harness must be Codex Desktop')
        self.base=self.config['rendererService'].rstrip('/')
        if urlsplit(self.base).hostname not in ('localhost','127.0.0.1'): raise ValueError('Renderer must be loopback')
        self.run=Path(self.config['resultDirectory']).resolve()
        self.directory=self.run/'mcp'/str(uuid.uuid4()); self.directory.mkdir(parents=True)
        self.session=None; self.calls=0
        self.record('connection_open',pid=os.getpid(),config=str(self.config_path),scope=self.config.get('scope','manual-desktop-experiment'))

    def record(self, kind, **data):
        with (self.directory/'events.jsonl').open('a',encoding='utf-8') as output:
            output.write(json.dumps({'time':time.time(),'kind':kind,**data},ensure_ascii=True)+'\n')

    def request(self, path, body=None):
        raw=None if body is None else json.dumps(body,allow_nan=False).encode('utf-8')
        req=urllib.request.Request(self.base+path,data=raw,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=self.config.get('toolTimeoutSeconds',120)) as response: return response.read()

    def tools(self):
        canonical=json.loads(self.request('/api/tools'))
        return [
            {'name':'desktop_status','description':'Read the configured model/endpoint and actual local recording status. This does not establish which model you are. Check recorded inference route, never a model self-report.', 'inputSchema':object_schema({})},
            {'name':'begin_task','description':'Start a fresh task on the volume selected by the Windows launcher. Import and render the actual initial PNG using the canonical service. This resets this MCP connection to a NEW session and never chooses visualization parameters. Call first for every new task; do not call again during an active task. Returns actual PNG image content.','inputSchema':object_schema({'goal':{'type':'string','minLength':1,'maxLength':12000}},['goal'])},
            *canonical]

    def image_result(self, data):
        # Human-readable artifact links supplement actual MCP image content.
        content=[{'type':'text','text':json.dumps(data,allow_nan=False)}]
        if data.get('imageUrl'):
            raw=self.request(data['imageUrl'])
            if hashlib.sha256(raw).hexdigest()!=data['imageSha256']: raise ValueError('Actual PNG hash mismatch')
            content.append({'type':'image','mimeType':'image/png','data':base64.b64encode(raw).decode('ascii')})
        return {'content':content,'isError':False}

    def call(self, name, arguments):
        self.calls+=1; index=self.calls; started=time.time()
        prefix=self.directory/('call-%04d'%index)
        request={'name':name,'arguments':arguments,'sessionId':self.session}
        prefix.with_suffix('.request.json').write_text(json.dumps(request,indent=2),encoding='utf-8')
        self.record('tool_start',call=index,**request)
        try:
            from isolation_guard import require_unchanged
            require_unchanged(self.run)
            if not isinstance(arguments,dict): raise ValueError('Arguments must be an object')
            if name=='desktop_status':
                if arguments: raise ValueError('desktop_status accepts no arguments')
                status={'formalHarness':'Codex Desktop','configuredModel':self.config['model'],'inferenceEndpoint':self.config['endpoint'],'recordingProxy':self.config['recordingProxy'],'resultDirectory':str(self.run),'sessionId':self.session,'desktopAcceptance':'pending human verification'}
                status.update(mcpConfigPath=str(self.config_path),configuredDesktopHome=self.config.get('desktopHome'),configuredGuiStateDirectory=str(self.run/'electron-user-data'),profileId=self.run.name)
                try:
                    with urllib.request.urlopen(self.config['recordingProxy']+'/desktop/status',timeout=5) as response: status['observedTransport']=json.load(response)
                except Exception as error: status['transportStatusError']=str(error)
                result=self.image_result(status)
            elif name=='begin_task':
                if set(arguments)!={'goal'} or type(arguments['goal'])!=str or not 0<len(arguments['goal'].strip())<=12000: raise ValueError('A bounded nonempty goal is required')
                if self.session:
                    old=json.loads(self.request('/api/session/'+self.session))
                    if old['state']=='running': raise ValueError('Current task is active. Finish or stop it before starting a new task.')
                data=json.loads(self.request('/api/tools/open',{'source':self.config['volumePath'],'goal':arguments['goal'],'model':self.config['model'],'budget':self.config['renderBudget']}))
                self.session=data['id']
                link={'time':time.time(),'sessionId':self.session,'connectionDirectory':str(self.directory),'serviceDirectory':str(Path(__file__).resolve().parents[1]/'runtime'/self.session),'goal':arguments['goal'],'scope':self.config.get('scope','manual-desktop-experiment')}
                (self.directory/'session.json').write_text(json.dumps(link,indent=2),encoding='utf-8')
                with (self.run/'sessions.jsonl').open('a',encoding='utf-8') as output: output.write(json.dumps(link)+'\n')
                result=self.image_result(data)
            else:
                if not self.session: raise ValueError('No volume task on this connection. Call begin_task with the user goal first.')
                data=json.loads(self.request('/api/tools/call',{'id':self.session,'name':name,'arguments':arguments}))
                if name=='finish_task':
                    selected=data['selectedRevision']
                    # Include the exact selected actual PNG as final GUI content too.
                    data['imageUrl']='/artifact/'+self.session+'/frame-'+str(selected)+'/image.png'
                result=self.image_result(data)
        except Exception as error:
            detail=error.read().decode('utf-8',errors='replace') if isinstance(error,urllib.error.HTTPError) else str(error)
            result={'content':[{'type':'text','text':detail}],'isError':True}
        raw=json.dumps(result,ensure_ascii=True).encode('utf-8')
        prefix.with_suffix('.result.json').write_bytes(raw)
        self.record('tool_end',call=index,name=name,sessionId=self.session,seconds=time.time()-started,isError=result['isError'],resultSha256=hashlib.sha256(raw).hexdigest())
        return result


def serve(config_path):
    controller=DesktopMcp(config_path)
    try:
        for line in sys.stdin:
            message=None
            try:
                message=json.loads(line)
                if 'id' not in message: continue
                method=message.get('method'); params=message.get('params',{})
                if method=='initialize':
                    result={'protocolVersion':'2024-11-05','capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'gav-desktop-renderer','version':'1.1.0'}}
                elif method=='ping': result={}
                elif method=='tools/list': result={'tools':controller.tools()}
                elif method=='tools/call': result=controller.call(params['name'],params.get('arguments',{}))
                else:
                    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'Method not found'}}),flush=True);continue
                print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
            except Exception as error:
                controller.record('protocol_error',message=str(error))
                print(json.dumps({'jsonrpc':'2.0','id':message.get('id') if isinstance(message,dict) else None,'error':{'code':-32603,'message':str(error)}}),flush=True)
    finally: controller.record('connection_closed',sessionId=controller.session)

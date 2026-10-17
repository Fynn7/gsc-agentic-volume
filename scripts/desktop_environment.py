"""Passive Desktop support: config, recording proxy and native service, never a harness."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from run_local_agent import InferenceRecorder, http, monitor

ROOT = Path(__file__).resolve().parents[1]
HOME_CONFIG = Path.home()/'.codex'/'config.toml'
ACTIVE = ROOT/'runtime/desktop/active.json'
VOLATILE_CONFIG_KEYS = frozenset({'mcp_servers.node_repl.env.SKY_CUA_NATIVE_PIPE_DIRECTORY'})


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def flatten(value, prefix=''):
    result={}
    for key, item in value.items():
        name=prefix+'.'+key if prefix else key
        if isinstance(item,dict): result.update(flatten(item,name))
        else: result[name]=item
    return result


def changed_config_keys(first, second):
    a=flatten(tomllib.loads(first.decode('utf-8-sig')))
    b=flatten(tomllib.loads(second.decode('utf-8-sig')))
    return sorted(key for key in set(a)|set(b) if (key in a)!=(key in b) or a.get(key)!=b.get(key))


def close_installation(pointer, directory, meta, status, differences=()):
    record={'time':time.time(),'status':status,'currentConfigSha256':digest(HOME_CONFIG) if HOME_CONFIG.exists() else None,'changedKeysFromOriginal':list(differences),'pointer':pointer}
    write_json(directory/'installation-closed.json',record)
    meta.update(installed=False,restoration=record)
    write_json(directory/'preparation.json',meta)
    (ACTIVE.parent/'installed.json').unlink()


def reconcile_manual_restore():
    """Clear stale bookkeeping only; never writes a user configuration."""
    path=ACTIVE.parent/'installed.json'
    if not path.exists(): return True
    pointer=json.loads(path.read_text());directory=Path(pointer['directory'])
    meta=json.loads((directory/'preparation.json').read_text())
    backup=directory/'user-config.original.toml'
    expected=meta['originalConfigSha256']
    if (expected is not None and digest(backup)!=expected) or (expected is None and backup.read_bytes()!=b''):
        raise ValueError('Original backup hash mismatch; no changes made')
    if not HOME_CONFIG.exists():
        if expected is not None:return False
        close_installation(pointer,directory,meta,'manual-restoration-reconciled-without-config-write');return True
    differences=changed_config_keys(backup.read_bytes(),HOME_CONFIG.read_bytes())
    # Only allow the known per-launch pipe value to differ, never another MCP key.
    if not set(differences)<=VOLATILE_CONFIG_KEYS: return False
    close_installation(pointer,directory,meta,'manual-restoration-reconciled-without-config-write',differences)
    return True


def patch_toml(text, edits):
    """Change explicit section keys, preserving unrelated user text and tables."""
    lines=text.splitlines(); section=''; found=set(); output=[]
    for line in lines:
        stripped=line.strip()
        if stripped.startswith('['):
            for key,value in edits.get(section,{}).items():
                if (section,key) not in found: output.append(key+' = '+json.dumps(value)); found.add((section,key))
            section=stripped[1:-1]
        key=line.split('=',1)[0].strip() if '=' in line and not stripped.startswith('#') else None
        if key in edits.get(section,{}):
            output.append(key+' = '+json.dumps(edits[section][key])); found.add((section,key))
        else: output.append(line)
    for key,value in edits.get(section,{}).items():
        if (section,key) not in found: output.append(key+' = '+json.dumps(value)); found.add((section,key))
    for table,values in edits.items():
        missing={k:v for k,v in values.items() if (table,k) not in found}
        if missing:
            if not table: raise ValueError('Top-level insertion failed')
            output.extend(['','['+table+']',*[k+' = '+json.dumps(v) for k,v in missing.items()]])
    result='\n'.join(output)+'\n'; tomllib.loads(result); return result


def catalog(config, directory):
    # Reuse the installed client's catalog schema, not GPT's instructions/capabilities.
    cached=json.loads((Path.home()/'.codex/models_cache.json').read_text(encoding='utf-8'))
    model=copy.deepcopy(cached['models'][0])
    model.update(slug=config['model'],display_name='Local VLM: '+config['model'],
                 description='Recorded Ollama route; Desktop acceptance pending human verification.',
                 default_reasoning_level='none',supported_reasoning_levels=[{'effort':'none','description':'No reasoning effort request'}],
                 context_window=config['contextSize'],max_context_window=config['contextSize'],
                 input_modalities=['text','image'],supports_search_tool=False,supports_image_detail_original=False,
                 visibility='list',supported_in_api=True,priority=0,upgrade=None,
                 experimental_supported_tools=[],additional_speed_tiers=[],service_tiers=[],
                 node_repl_disabled=True,supports_reasoning_effort_updates=False,
                 use_responses_lite=False)
    for key in ['tool_mode','multi_agent_version','multi_agent_reasoning_effort','comp_hash','availability_nux','available_access_programs']:
        model.pop(key,None)
    # Keep nullable/structured policy fields shaped like this installed catalog.
    messages=model.get('model_messages')
    if isinstance(messages,dict):
        messages['persistent_instructions']='Use the volume_renderer MCP tools to operate the real native volume renderer. Observe returned image content before choosing another action. Report unknowns honestly.'
        messages['instructions_template']=messages['persistent_instructions']
    path=directory/'models.json'; write_json(path,{'models':[model]})
    write_json(directory/'catalog-provenance.json',{'sourceClientVersion':cached.get('client_version'),'sourceCacheSha256':digest(Path.home()/'.codex/models_cache.json'),'desktopParsing':'pending human verification','model':config['model']})
    return path


def prepare(template, volume=None):
    if not reconcile_manual_restore(): raise ValueError('Unresolved legacy global installation. Inspect configuration differences before preparing another run.')
    config=json.loads(Path(template).read_text(encoding='utf-8-sig'))
    if config.get('formalHarness')!='Codex Desktop': raise ValueError('Only Codex Desktop is allowed')
    if ':cloud' in config['model']: raise ValueError('Cloud model prohibited')
    for key in ['endpoint','rendererService','recordingProxy']:
        if urlsplit(config[key]).hostname not in ('localhost','127.0.0.1'): raise ValueError(key+' must use loopback (SSH tunnel for remote inference)')
    source=Path(volume or config['volumePath'])
    if not source.is_absolute(): source=ROOT/source
    if not source.is_file(): raise ValueError('Volume header does not exist: '+str(source))
    config['volumePath']=str(source.resolve())
    directory=ROOT/'runtime/desktop/runs'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    config['resultDirectory']=str(directory); config['python']=sys.executable
    (ACTIVE.parent/'workspace').mkdir(parents=True,exist_ok=True)
    (directory/'gui').mkdir()
    desktop_home=directory/'desktop-home';desktop_home.mkdir()
    config['desktopHome']=str(desktop_home)
    config['desktopIsolation']='candidate only; not verified in Desktop; automatic GUI launch disabled'
    write_json(directory/'config.json',config)
    # Start from an EMPTY config. Do not copy daily auth, notifications, apps or MCPs.
    edits={'':{'model':config['model'],'model_provider':'volume_local','model_catalog_json':str(catalog(config,directory)),
               'model_context_window':config['contextSize'],'model_reasoning_effort':'none',
               'project_doc_max_bytes':0,'web_search':'disabled','service_tier':'default','approvals_reviewer':'user'},
           'features':{'shell_tool':False,'plugins':False,'multi_agent':False,'goals':False,'skip_host_skill_discovery':True,'js_repl':False,'memories':False},
           'model_providers.volume_local':{'name':'Recorded Ollama for volume experiments','base_url':config['recordingProxy']+'/v1',
               'wire_api':'responses','requires_openai_auth':False,'stream_idle_timeout_ms':config['timeoutSeconds']*1000,'request_max_retries':0,'stream_max_retries':0},
           'mcp_servers.volume_renderer':{'enabled':True,'command':sys.executable,'args':['-B',str(ROOT/'scripts/renderer_mcp.py'),'--desktop-config',str(directory/'config.json')],
               'tool_timeout_sec':config['toolTimeoutSeconds'],'startup_timeout_sec':30,'default_tools_approval_mode':'approve'}}
    candidate=patch_toml('',edits)
    (directory/'codex-desktop.config.toml').write_text(candidate,encoding='utf-8')
    (desktop_home/'config.toml').write_text(candidate,encoding='utf-8')
    write_json(directory/'preparation.json',{'originalConfigSha256':digest(HOME_CONFIG) if HOME_CONFIG.exists() else None,'prepared':time.time(),'installed':False,'desktopAcceptance':'pending human verification'})
    write_json(ACTIVE,config)
    (ACTIVE.parent/'LATEST.txt').write_text(str(directory),encoding='utf-8')
    print(str(directory),flush=True)
    return config


def install(config):
    raise ValueError('Global Codex config installation is retired. Desktop isolation must be verified before any GUI experiment. Use prepare/services-only for support work.')


def restore():
    if not (ACTIVE.parent/'installed.json').exists():
        print('No legacy installation is pending. User configuration unchanged.');return
    if reconcile_manual_restore():
        print('Manual restoration recognized; stale installation state cleared. User configuration unchanged.');return
    pointer=json.loads((ACTIVE.parent/'installed.json').read_text()); directory=Path(pointer['directory'])
    meta=json.loads((directory/'preparation.json').read_text())
    if digest(HOME_CONFIG)!=meta['installedSha256']: raise ValueError('Config changed after installation; compare backup manually: '+str(directory/'user-config.original.toml'))
    if meta['originalExisted']: HOME_CONFIG.write_bytes((directory/'user-config.original.toml').read_bytes())
    else: HOME_CONFIG.unlink()
    close_installation(pointer,directory,meta,'restored-exact-legacy-installation')
    print('Original user config restored. Restart Codex Desktop.')


def serve(config):
    directory=Path(config['resultDirectory']); stop=threading.Event(); owned=None
    if urlsplit(config['endpoint']).hostname not in ('localhost','127.0.0.1'): raise ValueError('Use an SSH tunnel')
    inventory={'version':http(config['endpoint'],'/api/version'), 'show':http(config['endpoint'],'/api/show',{'model':config['model']}),'tags':http(config['endpoint'],'/api/tags')}
    if 'vision' not in inventory['show'].get('capabilities',[]) or inventory['show'].get('remote_host'): raise ValueError('Installed local vision model required')
    write_json(directory/'model.json',inventory)
    try:
        package=subprocess.check_output(['powershell.exe','-NoProfile','-Command',"Get-AppxPackage -Name OpenAI.Codex | Select-Object Name,Version,PackageFullName | ConvertTo-Json -Compress"],text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=20).strip()
        write_json(directory/'desktop-package.json',json.loads(package))
    except Exception as error: write_json(directory/'desktop-package.json',{'unverified':str(error),'manualVersionScreenshotRequired':True})
    port=urlsplit(config['rendererService']).port
    try: identity=http(config['rendererService'],'/api/service',timeout=2)
    except Exception:
        owned=subprocess.Popen([sys.executable,'-B',str(ROOT/'src/app.py'),'--port',str(port)],cwd=ROOT,stdout=(directory/'renderer-stdout.log').open('wb'),stderr=(directory/'renderer-stderr.log').open('wb'),creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(60):
            try: identity=http(config['rendererService'],'/api/service',timeout=1); break
            except Exception:
                if owned.poll() is not None: raise RuntimeError('Renderer failed; inspect renderer-stderr.log')
                time.sleep(.25)
        else: raise RuntimeError('Renderer startup timeout')
    if identity.get('service')!='gav-native-renderer': raise ValueError('Wrong renderer identity')
    recorder=InferenceRecorder(config['endpoint'],directory/'http',config)
    parent=recorder.handler()
    class Handler(parent):
        def do_GET(self):
            if self.path=='/desktop/status':
                records=[]
                for path in (directory/'http').glob('*.metadata.json'):
                    try: records.append(json.loads(path.read_text()))
                    except (OSError,ValueError): pass # A metadata write may still be in progress.
                calls=[r for r in records if r['method']=='POST' and r['path'] in ['/v1/responses','/v1/chat/completions','/api/chat']]
                raw=json.dumps({'formalHarness':'Codex Desktop','model':config['model'],'endpoint':config['endpoint'],'resultDirectory':str(directory),'pid':os.getpid(),'inferenceRequests':len(calls),'successfulInferenceResponses':sum(r.get('status')==200 for r in calls),'desktopAcceptance':'pending human verification'}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            else: super().do_GET()
        def do_POST(self):
            if self.path=='/desktop/stop':
                self.send_response(200);self.send_header('Content-Length','0');self.end_headers();threading.Thread(target=server.shutdown,daemon=True).start()
            else: super().do_POST()
    server=ThreadingHTTPServer(('127.0.0.1',urlsplit(config['recordingProxy']).port),Handler)
    threading.Thread(target=monitor,args=(stop,directory,config['endpoint']),daemon=True).start()
    write_json(directory/'ready.json',{'pid':os.getpid(),'started':time.time(),'renderer':identity,'rendererPid':owned.pid if owned else None,'desktopAcceptance':'pending human verification','sourceHashes':{str(p.relative_to(ROOT)):digest(p) for p in [ROOT/'scripts/desktop_mcp.py',ROOT/'scripts/renderer_mcp.py',ROOT/'scripts/desktop_environment.py',ROOT/'src/renderer.py',ROOT/'src/external_tools.py',ROOT/'VirtualSampling/Raycaster/VS/x64/Release/Raycaster.exe']}})
    try: server.serve_forever()
    finally:
        stop.set();server.server_close()
        if owned: owned.terminate();owned.wait(timeout=10)
        write_json(directory/'stopped.json',{'time':time.time()})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','install','restore','serve']);parser.add_argument('--template');parser.add_argument('--volume');parser.add_argument('--config',default=str(ACTIVE));args=parser.parse_args()
    if args.action=='prepare': prepare(args.template,args.volume)
    elif args.action=='restore': restore()
    else:
        config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        if args.action=='install': install(config)
        else: serve(config)


if __name__=='__main__': main()

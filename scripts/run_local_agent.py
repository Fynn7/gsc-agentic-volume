"""Run an existing harness with optional MCP and lossless local HTTP recording.

This launcher never chooses visualization actions or calls a model in a loop.
The selected installed harness owns all decisions and multimodal tool feedback.
"""
import argparse
import base64
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from agent_console import ConsoleTrace


def http(base, path, body=None, timeout=120):
    raw = None if body is None else json.dumps(body, allow_nan=False).encode()
    req = urllib.request.Request(base.rstrip('/') + path, data=raw, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response: return json.load(response)


class InferenceRecorder:
    def __init__(self, endpoint, directory, config):
        self.endpoint = endpoint.rstrip('/')
        self.directory = directory; directory.mkdir()
        self.config = config; self.count = 0; self.lock = threading.Lock()

    def handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.forward()
            def do_POST(self): self.forward()
            def forward(self):
                if not self.path.startswith('/') or self.path.startswith('//'):
                    self.send_error(403, 'Only local inference paths are allowed'); return
                with owner.lock:
                    owner.count += 1; index = owner.count
                prefix = owner.directory / ('http-%03d' % index)
                started = time.time(); request_body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                prefix.with_suffix('.request.bin').write_bytes(request_body)
                record = {'index': index, 'method': self.command, 'path': self.path, 'started': started}
                try:
                    outgoing = request_body
                    if self.command == 'POST' and self.path in ('/v1/responses', '/v1/chat/completions', '/api/chat'):
                        body = json.loads(request_body)
                        if body.get('model') != owner.config['model']: raise ValueError('Unexpected model; fallback is prohibited')
                        body['temperature'] = owner.config.get('temperature', 0)
                        if self.path == '/v1/responses':
                            body['think'] = owner.config.get('think', False)
                            body['max_output_tokens'] = owner.config.get('maxOutputTokens', 1536)
                        elif self.path == '/v1/chat/completions':
                            body['reasoning_effort'] = 'none' if not owner.config.get('think', False) else 'medium'
                            body['max_tokens'] = owner.config.get('maxOutputTokens', 1536)
                        outgoing = json.dumps(body, allow_nan=False).encode()
                    prefix.with_suffix('.forwarded.bin').write_bytes(outgoing)
                    headers = {key: value for key, value in self.headers.items() if key.lower() not in ('host', 'content-length', 'connection', 'authorization')}
                    req = urllib.request.Request(owner.endpoint + self.path, data=outgoing if self.command == 'POST' else None, method=self.command, headers=headers)
                    response = urllib.request.urlopen(req, timeout=owner.config['timeoutSeconds'])
                except urllib.error.HTTPError as error:
                    response = error
                except Exception as error:
                    record['error'] = str(error); data = json.dumps({'error': str(error)}).encode()
                    self.send_response(502); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
                    record['ended'] = time.time(); prefix.with_suffix('.metadata.json').write_text(json.dumps(record, indent=2)); return
                try:
                    record['status'] = response.status
                    self.send_response(response.status)
                    for key, value in response.headers.items():
                        if key.lower() not in ('connection', 'transfer-encoding', 'content-encoding'): self.send_header(key, value)
                    self.end_headers()
                    with prefix.with_suffix('.response.bin').open('wb') as output:
                        while True:
                            chunk = response.read1(65536)
                            if not chunk: break
                            if 'firstResponseByte' not in record: record['firstResponseByte'] = time.time()
                            output.write(chunk); output.flush(); self.wfile.write(chunk); self.wfile.flush()
                except Exception as error: record['transportError'] = str(error)
                finally:
                    response.close(); record['ended'] = time.time(); record['seconds'] = record['ended'] - started
                    prefix.with_suffix('.metadata.json').write_text(json.dumps(record, indent=2))
        return Handler


def monitor(stop, directory, endpoint):
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in ['totalPhysical','availablePhysical','totalPage','availablePage','totalVirtual','availableVirtual','availableExtended']]
    class Times(ctypes.Structure):
        _fields_ = [('value', ctypes.c_ulonglong)]
    with (directory / 'telemetry.jsonl').open('w') as output:
        while not stop.is_set():
            item = {'time': time.time()}
            try:
                memory = Memory(); memory.length = ctypes.sizeof(memory)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
                idle, kernel, user = Times(), Times(), Times()
                ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
                item.update(ramTotalBytes=memory.totalPhysical, ramAvailableBytes=memory.availablePhysical, commitLimitBytes=memory.totalPage, commitAvailableBytes=memory.availablePage, cpuTimes={'idle':idle.value,'kernel':kernel.value,'user':user.value})
                item['gpu'] = subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,memory.total,utilization.gpu,utilization.memory,temperature.gpu,power.draw','--format=csv,noheader,nounits'],text=True,creationflags=subprocess.CREATE_NO_WINDOW).strip()
                item['ollama'] = http(endpoint, '/api/ps', timeout=5)
            except Exception as error: item['error'] = str(error)
            output.write(json.dumps(item) + '\n'); output.flush(); stop.wait(3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--show-images', action='store_true', help='print a terminal thumbnail for every real PNG')
    parser.add_argument('--open-images', action='store_true', help='open every real PNG in the system image viewer')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    run_started=time.time()
    if ':cloud' in config['model']: raise ValueError('Cloud model prohibited')
    endpoint = config['endpoint'].rstrip('/'); service = config['rendererService'].rstrip('/')
    if urlsplit(service).hostname not in ('127.0.0.1', 'localhost'): raise ValueError('Renderer service must be local')
    directory = Path(config['resultDirectory']).resolve(); directory.mkdir(parents=True, exist_ok=False)
    (directory / 'config.json').write_text(json.dumps(config, indent=2))
    inventory = {'version': http(endpoint, '/api/version'), 'tags': http(endpoint, '/api/tags'), 'show': http(endpoint, '/api/show', {'model': config['model']})}
    if 'vision' not in inventory['show'].get('capabilities', []) or inventory['show'].get('remote_host'): raise ValueError('Requested model must be an installed local VLM')
    (directory / 'model.json').write_text(json.dumps(inventory, indent=2))
    identity = http(service, '/api/service')
    if identity.get('service') != 'gav-native-renderer': raise ValueError('Wrong renderer service')
    session = http(service, '/api/tools/open', {'source': config['volumePath'], 'goal': config['task'], 'model': config['model'], 'budget': config['renderBudget']})
    (directory / 'initial.json').write_text(json.dumps(session, indent=2))
    print(json.dumps({'event': 'session_opened', 'id': session['id'], 'directory': str(directory)}), flush=True)
    trace = ConsoleTrace(directory, ROOT / 'runtime' / session['id'], show_images=args.show_images, open_images=args.open_images)
    recorder = InferenceRecorder(endpoint, directory / 'http', config)
    server = ThreadingHTTPServer(('127.0.0.1', 0), recorder.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy = 'http://127.0.0.1:' + str(server.server_port)
    env = dict(os.environ)
    # Isolate child integrations and fail closed on accidental public endpoints.
    for key in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','GOOGLE_API_KEY','GEMINI_API_KEY','OLLAMA_API_KEY']:
        env.pop(key, None)
    env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy, NO_PROXY='127.0.0.1,localhost', DO_NOT_TRACK='1')
    stop = threading.Event(); telemetry = threading.Thread(target=monitor, args=(stop,directory,endpoint), daemon=True); telemetry.start()
    python = config.get('python', sys.executable)
    mcp_args = ['-B', str(ROOT / 'scripts/renderer_mcp.py'), '--service', service, '--session', session['id']]
    workspace = directory / 'workspace'; workspace.mkdir()
    harness = config.get('harness', 'codex')
    task = ('You operate the real Native Raycaster using only the renderer MCP tools. First inspect_scene to receive its actual PNG and numeric facts. '
            'Choose each next operation after inspecting the newest image. A PNG path alone is not an image. Omitted parameters must remain unchanged. '
            'Use expectedRevision from the latest result. Do not invent anatomical orientation or assert task success from JSON validity or nonblack pixels. '
            'Respect the observation budget. Save the best actual frame with finish_task, including honest limitations. Do not use shell or filesystem tools.\nUser task: ' + config['task'])
    if harness == 'codex':
        env['CODEX_HOME'] = str(directory / 'codex-home'); Path(env['CODEX_HOME']).mkdir()
        env['OLLAMA_BASE_URL'] = proxy
        executable = config.get('codexExecutable')
        if not executable:
            shim = Path(shutil.which('codex.cmd') or shutil.which('codex') or 'codex')
            native = shim.parent / 'node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
            executable = str(native) if native.is_file() else str(shim)
        if Path(executable).suffix.lower() in ('.cmd','.bat','.ps1'): raise ValueError('Configure a native CLI executable; Windows batch wrappers can truncate multiline prompts')
        command = [executable, 'exec', task, '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '-C', str(workspace), '-s', 'read-only', '-m', config['model'], '--json']
        overrides = {'model_context_window': config['contextSize'], 'model_reasoning_effort': 'none' if not config.get('think',False) else 'medium', 'features.shell_tool': False,
                     'project_doc_max_bytes':config.get('projectDocumentBytes',0),
                     'mcp_servers.renderer.command': python, 'mcp_servers.renderer.args': mcp_args, 'mcp_servers.renderer.tool_timeout_sec': 120,
                     'mcp_servers.renderer.default_tools_approval_mode':'approve',
                     'model_provider':'local_qwen', 'model_providers.local_qwen.name':'Recorded local Ollama', 'model_providers.local_qwen.base_url':proxy+'/v1',
                     'model_providers.local_qwen.wire_api':'responses', 'model_providers.local_qwen.requires_openai_auth':False, 'features.plugins':False,
                     'features.multi_agent':False, 'features.goals':False, 'features.skip_host_skill_discovery':True, 'web_search':'disabled',
                     'model_providers.local_qwen.stream_idle_timeout_ms': int(config['timeoutSeconds'] * 1000), 'model_providers.local_qwen.request_max_retries': 0, 'model_providers.local_qwen.stream_max_retries': 0}
        for key, value in overrides.items(): command += ['-c', key + '=' + json.dumps(value)]
    else: raise ValueError('Only the validated Codex route is implemented; alternate harnesses require a real compatibility diagnosis')
    manifest = {'harness':harness,'command':command,'runStarted':run_started,'started':time.time(),'sessionId':session['id'],'rendererService':identity,'taskSuccess':'unverified','protocolTranslation':False,'inferenceOverrides':{k:config.get(k) for k in ['temperature','think','maxOutputTokens']}}
    manifest['harnessVersion']=subprocess.check_output([executable,'--version'],env=env,text=True,creationflags=subprocess.CREATE_NO_WINDOW).strip()
    manifest['sourceHashes']={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in [ROOT/'src/app.py',ROOT/'src/renderer.py',ROOT/'src/volume.py',ROOT/'src/external_tools.py',ROOT/'scripts/renderer_mcp.py',ROOT/'scripts/run_local_agent.py']}
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2))
    trace.start()
    try:
        with (directory/'harness.jsonl').open('wb') as stdout, (directory/'harness-stderr.log').open('wb') as stderr:
            process = subprocess.Popen(command, cwd=workspace, env=env, stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
            manifest['pid']=process.pid
            try: manifest['exitCode']=process.wait(timeout=config['taskTimeoutSeconds'])
            except subprocess.TimeoutExpired:
                manifest['timedOut']=True
                subprocess.run(['taskkill','/pid',str(process.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                process.wait(); manifest['exitCode']=process.returncode
    finally:
        stop.set(); telemetry.join(10); server.shutdown(); server.server_close()
        manifest.update(ended=time.time(),httpRequests=recorder.count)
        manifest['seconds']=manifest['ended']-manifest['started']
        manifest['totalSeconds']=manifest['ended']-manifest['runStarted']
        state=http(service,'/api/session/'+session['id'])
        (directory/'session.json').write_text(json.dumps(state,indent=2))
        manifest['state']=state['state']
        (directory/'manifest.json').write_text(json.dumps(manifest,indent=2))
        trace.stop()
        trace.poll_once()
        trace.summary(manifest)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        import traceback
        failure={'status':'failed','error':str(error),'traceback':traceback.format_exc(),'time':time.time(),'taskSuccess':'unverified'}
        if isinstance(error,urllib.error.HTTPError): failure['response']=error.read().decode(errors='replace')
        try:
            cfg=json.loads(Path(sys.argv[sys.argv.index('--config')+1]).read_text(encoding='utf-8-sig'))
            target=Path(cfg['resultDirectory'])
            if target.is_dir(): (target/'failure.json').write_text(json.dumps(failure,indent=2))
        except Exception: pass
        print(json.dumps(failure),file=sys.stderr); sys.exit(1)

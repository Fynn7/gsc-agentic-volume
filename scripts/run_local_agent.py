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
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from agent_console import ConsoleTrace
from run_lifecycle import (ConfigError, ReservationError, RunConflictError, RunLock, TASK_FIELDS,
                           reserve_directory, resolve_run_base, task_key, validate_config)

# One lock per logical task, shared by independent launcher processes.
LOCK_DIRECTORY = ROOT / 'runtime' / 'locks'
CONTROL_DIRECTORY = ROOT / 'runtime' / 'tui-control'


def stop_requested(run_id):
    """Cooperative stop flag written by the TUI Stop button (read-only evidence otherwise)."""
    try:
        return (CONTROL_DIRECTORY / ('%s.stop' % run_id)).is_file()
    except OSError:
        return False
PROVENANCE_SOURCES = [ROOT / 'src/app.py', ROOT / 'src/renderer.py', ROOT / 'src/volume.py',
                      ROOT / 'src/external_tools.py', ROOT / 'scripts/renderer_mcp.py',
                      ROOT / 'scripts/run_local_agent.py', ROOT / 'scripts/run_lifecycle.py']


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def stop_harness(process):
    """Kill the harness and every child it spawned (Ctrl+C / timeout).

    Uses `/T` so codex's MCP server children die too; a follow-up Ctrl+C must
    not abort the kill, so SIGINT is ignored for the rest of this process.
    """
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/pid', str(process.pid), '/T', '/F'],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.terminate()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


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


def load_config(path):
    """Read the original config file bytes and parse them; never writes to it."""
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise ConfigError('Cannot read configuration file %s: %s' % (path, error)) from error
    try:
        config = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError) as error:
        raise ConfigError('Configuration %s is not valid JSON: %s' % (path, error)) from error
    return raw, config


class RunContext:
    """A reserved, provenance-complete run produced by prepare_run and used by execute."""
    __slots__ = ('config', 'directory', 'run_id', 'source', 'record', 'lock')

    def __init__(self, config, directory, run_id, source, record, lock):
        self.config = config; self.directory = directory; self.run_id = run_id
        self.source = source; self.record = record; self.lock = lock


def prepare_run(config, config_path, raw):
    """Validate, take the task lock and atomically reserve a fresh run directory.

    Makes no model request. ConfigError / RunConflictError / ReservationError are
    raised before any directory exists, so a rejected launch leaves no evidence
    behind and never touches a previous experiment.
    """
    validate_config(config)
    source = Path(config_path).resolve()
    key = task_key(config)
    lock = RunLock(key, LOCK_DIRECTORY)
    lock.acquire({'pid': os.getpid(), 'taskKey': key, 'configSource': str(source),
                  'logicalTask': {field: config.get(field) for field in TASK_FIELDS},
                  'phase': 'preflight', 'mode': 'new-experiment', 'startedAt': time.time()})
    try:
        configured = Path(config['resultDirectory']).resolve()
        base, adjusted = resolve_run_base(configured)
        if adjusted:
            print(json.dumps({'event': 'run_base_adjusted', 'configuredResultDirectory': str(configured),
                              'resultBase': str(base),
                              'reason': 'configured path already holds experiment evidence; never written into'}),
                  flush=True)
        directory, run_id = reserve_directory(base)
        effective = dict(config)
        effective['resultDirectory'] = str(directory)
        record = {'runId': run_id, 'mode': 'new-experiment', 'status': 'running',
                  'pid': os.getpid(), 'taskKey': key, 'directory': str(directory),
                  'resultBase': str(base), 'resultBaseAdjusted': adjusted,
                  'configuredResultDirectory': str(configured),
                  'configSource': str(source),
                  'configSha256': hashlib.sha256(raw).hexdigest(),
                  'originalResultDirectory': config.get('resultDirectory'), 'startedAt': time.time(),
                  'resume': 'not implemented: every invocation is a new experiment, never a resumed one'}
        write_json(directory / 'run.json', record)
        (directory / 'config.source.json').write_bytes(raw)   # exact original config bytes, preserved
        write_json(directory / 'config.json', effective)      # effective config actually used this run
        (base / 'LATEST.txt').write_text(str(directory), encoding='utf-8')
        lock.update(directory=str(directory), runId=run_id, phase='reserved')
        return RunContext(effective, directory, run_id, source, record, lock)
    except Exception:
        lock.release()
        raise


def execute(context, show_images=False, open_images=False):
    config = context.config; directory = context.directory
    endpoint = config['endpoint'].rstrip('/'); service = config['rendererService'].rstrip('/')
    run_started = time.time()
    inventory = {'version': http(endpoint, '/api/version'), 'tags': http(endpoint, '/api/tags'), 'show': http(endpoint, '/api/show', {'model': config['model']})}
    if 'vision' not in inventory['show'].get('capabilities', []) or inventory['show'].get('remote_host'): raise ValueError('Requested model must be an installed local VLM')
    write_json(directory / 'model.json', inventory)
    identity = http(service, '/api/service')
    if identity.get('service') != 'gav-native-renderer': raise ValueError('Wrong renderer service')
    session = http(service, '/api/tools/open', {'source': config['volumePath'], 'goal': config['task'], 'model': config['model'], 'budget': config['renderBudget']})
    write_json(directory / 'initial.json', session)
    context.lock.update(phase='inference', sessionId=session['id'])
    print(json.dumps({'event': 'session_opened', 'runId': context.run_id, 'id': session['id'], 'directory': str(directory)}), flush=True)
    trace = ConsoleTrace(directory, ROOT / 'runtime' / session['id'], show_images=show_images, open_images=open_images)
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
    manifest = {'harness':harness,'command':command,'runStarted':run_started,'started':time.time(),'sessionId':session['id'],'rendererService':identity,'taskSuccess':'unverified','protocolTranslation':False,'runId':context.run_id,'resultDirectory':str(directory),'resultBase':context.record['resultBase'],'configSource':str(context.source),'configSha256':context.record['configSha256'],'mode':'new-experiment','inferenceOverrides':{k:config.get(k) for k in ['temperature','think','maxOutputTokens']}}
    manifest['harnessVersion']=subprocess.check_output([executable,'--version'],env=env,text=True,creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0).strip()
    manifest['sourceHashes']={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in PROVENANCE_SOURCES}
    write_json(directory/'manifest.json', manifest)
    trace.start()
    try:
        with (directory/'harness.jsonl').open('wb') as stdout, (directory/'harness-stderr.log').open('wb') as stderr:
            process = subprocess.Popen(command, cwd=workspace, env=env, stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            manifest['pid']=process.pid
            context.lock.update(harnessPid=process.pid, phase='harness-running')
            # Cooperative stop: the TUI Stop button writes runtime/tui-control/<runId>.stop.
            # Poll in short slices so a stop request terminates the harness tree and still
            # flows through the normal interrupted/failure.json path. No stop file means
            # the legacy timeout/KeyboardInterrupt behavior is unchanged.
            try:
                deadline = time.time() + float(config['taskTimeoutSeconds'])
                while True:
                    try:
                        manifest['exitCode']=process.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        if stop_requested(context.run_id):
                            manifest['interrupted']=True
                            manifest['stopReason']='tui-stop-request'
                            stop_harness(process)
                            manifest['exitCode']=process.returncode
                            raise KeyboardInterrupt('stop requested via runtime/tui-control/%s.stop' % context.run_id)
                        if time.time() >= deadline:
                            manifest['timedOut']=True
                            stop_harness(process)
                            manifest['exitCode']=process.returncode
                            break
            except KeyboardInterrupt:
                # Cooperative TUI stop already recorded above; hard Ctrl+C lands here too.
                if not manifest.get('interrupted'):
                    manifest['interrupted']=True
                    stop_harness(process)
                    manifest['exitCode']=process.returncode
                raise
    finally:
        stop.set(); telemetry.join(10); server.shutdown(); server.server_close()
        interrupted=bool(manifest.get('interrupted'))
        manifest.update(ended=time.time(),httpRequests=recorder.count)
        manifest['seconds']=manifest['ended']-manifest['started']
        manifest['totalSeconds']=manifest['ended']-manifest['runStarted']
        try: state=http(service,'/api/session/'+session['id'])
        except Exception as error: state={'id':session['id'],'state':'unavailable','error':str(error)}
        write_json(directory/'session.json', state)
        manifest['state']=state['state']
        write_json(directory/'manifest.json', manifest)
        trace.stop()
        trace.poll_once()
        trace.summary(manifest)
        context.record.update(status='failed' if interrupted else 'finished', ended=manifest['ended'], exitCode=manifest.get('exitCode'),
                              timedOut=bool(manifest.get('timedOut')), sessionId=session['id'])
        if interrupted:
            context.record['interrupted']=True
            context.record['error']='interrupted by Ctrl+C; harness and its child processes were terminated'
            write_json(directory/'failure.json', {'status':'interrupted','stage':'run','time':time.time(),
                                                  'error':context.record['error'],'taskSuccess':'unverified'})
        write_json(directory/'run.json', context.record)
    return exit_code_from_manifest(manifest)


def exit_code_from_manifest(manifest):
    """Propagate the harness result: 124 on timeout, otherwise its non-zero code."""
    if manifest.get('timedOut'): return 124
    code = manifest.get('exitCode')
    return int(code) if isinstance(code, int) and code != 0 else 0


def report_failure(stage, error, debug=False, response=None, directory=None):
    """Concise, traceback-free startup diagnostic; the full detail stays on disk."""
    if response is None and isinstance(error, urllib.error.HTTPError):
        response = error.read().decode(errors='replace')
    summary = {'status': 'failed', 'stage': stage, 'error': str(error), 'time': time.time(), 'taskSuccess': 'unverified'}
    if response: summary['response'] = response
    detail = str(error).strip()
    if response: detail += '\n  response: ' + ' '.join(response.split())[:400]
    sys.stderr.write('STARTUP FAILED (%s): %s\n' % (stage, detail))
    if directory is None:
        sys.stderr.write('  existing experiment directories were not modified; no earlier evidence was touched\n')
    else:
        sys.stderr.write('  this run\'s own evidence is kept in %s; existing experiment directories were not modified\n' % directory)
    if debug:
        summary['traceback'] = traceback.format_exc()
        sys.stderr.write(traceback.format_exc())
    else:
        summary['hint'] = 'run scripts/run_local_agent.py --config <file> --debug for the full Python traceback (run-stage detail is also in failure.json)'
    sys.stderr.write(json.dumps(summary, ensure_ascii=False) + '\n'); sys.stderr.flush()


def mark_failed(context, error):
    failure = {'status': 'failed', 'stage': 'run', 'error': str(error), 'time': time.time(),
               'traceback': traceback.format_exc(), 'taskSuccess': 'unverified'}
    if isinstance(error, urllib.error.HTTPError): failure['response'] = error.read().decode(errors='replace')
    write_json(context.directory / 'failure.json', failure)
    context.record.update(status='failed', ended=time.time(), error=str(error))
    write_json(context.directory / 'run.json', context.record)
    return failure


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run one CLI experiment in a fresh, unique result directory.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--show-images', action='store_true', help='print a terminal thumbnail for every real PNG')
    parser.add_argument('--open-images', action='store_true', help='open every real PNG in the system image viewer')
    parser.add_argument('--debug', action='store_true', help='include the full Python traceback on a startup failure')
    args = parser.parse_args(argv)
    try:
        raw, config = load_config(args.config)
        context = prepare_run(config, args.config, raw)
    except ConfigError as error:
        report_failure('configuration', error, args.debug); return 2
    except RunConflictError as error:
        report_failure('duplicate-run', error, args.debug); return 3
    except ReservationError as error:
        report_failure('reservation', error, args.debug); return 2
    except Exception as error:
        report_failure('startup', error, args.debug); return 1
    print(json.dumps({'event': 'run_started', 'runId': context.run_id, 'mode': 'new-experiment',
                      'directory': str(context.directory), 'configSource': str(context.source)}), flush=True)
    try:
        return execute(context, show_images=args.show_images, open_images=args.open_images)
    except KeyboardInterrupt:
        # execute() already killed the harness tree and recorded the interruption.
        return 130
    except Exception as error:
        failure = mark_failed(context, error)
        report_failure('run', error, args.debug, response=failure.get('response'), directory=context.directory)
        return 1
    finally:
        context.lock.release()


if __name__ == '__main__':
    sys.exit(main())

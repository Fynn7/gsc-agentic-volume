"""Local harness and thin GUI share the same real executor and model driver."""
import argparse
import json
import copy
import threading
import time
import uuid
import traceback
import mimetypes
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, unquote
from volume import prepare
from renderer import render, initial_scene, validate_scene, ROOT
from model import OllamaDriver, image_message
from external_tools import ExternalTools, TOOLS
RUNTIME = ROOT/'runtime'
RUNTIME.mkdir(exist_ok=True)
FRONTEND = ROOT/'frontend'/'dist'


def imports_root():
    root = RUNTIME/'volume-imports'
    root.mkdir(exist_ok=True)
    return root


VOLUME_IMPORTS = {}
VOLUME_IMPORTS_LOCK = threading.Lock()


def _import_artifact_url(path):
    rel = Path(path).resolve().relative_to(RUNTIME.resolve()).as_posix()
    return '/artifact/'+rel


def create_volume_import(source):
    import shutil
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Volume path required')
    with VOLUME_IMPORTS_LOCK:
        import_id = str(uuid.uuid4())
        directory = imports_root()/import_id
        directory.mkdir(parents=True, exist_ok=False)
        try:
            volume = prepare(source.strip(), directory/'volume')
        except Exception:
            try:
                shutil.rmtree(directory, ignore_errors=True)
            except Exception:
                pass
            raise
        record = {'importId': import_id, 'source': str(Path(source).resolve()) if Path(source).exists() else source.strip(),
                  'directory': str(directory), 'volume': volume,
                  'datUrl': _import_artifact_url(Path(volume['rendererPath']).resolve()),
                  'rawUrl': _import_artifact_url(Path(volume['rendererPath']).with_name('render.raw').resolve())}
        (directory/'import.json').write_text(json.dumps(
            {'importId': import_id, 'source': record['source'], 'volume': volume,
             'datUrl': record['datUrl'], 'rawUrl': record['rawUrl']}, indent=2))
        VOLUME_IMPORTS[import_id] = record
        return record


def get_volume_import(import_id):
    if not isinstance(import_id, str) or not import_id.strip():
        raise ValueError('importId required')
    key = import_id.strip()
    record = VOLUME_IMPORTS.get(key)
    if record is not None:
        return record
    # Reload from disk (survives harness restart within same runtime dir).
    candidate = imports_root()/key/'import.json'
    if candidate.is_file():
        try:
            data = json.loads(candidate.read_text())
            directory = imports_root()/key
            volume_path = directory/'volume'/'volume.json'
            volume = json.loads(volume_path.read_text()) if volume_path.is_file() else data.get('volume')
            record = {'importId': key, 'source': data.get('source', ''),
                      'directory': str(directory), 'volume': volume,
                      'datUrl': data.get('datUrl') or _import_artifact_url(directory/'volume'/'render.dat'),
                      'rawUrl': data.get('rawUrl') or _import_artifact_url(directory/'volume'/'render.raw')}
            VOLUME_IMPORTS[key] = record
            return record
        except Exception as error:
            raise ValueError(f'Volume import is unreadable: {error}')
    raise ValueError('Unknown volume import')


def pick_volume_file():
    """Open the OS-native file chooser on the harness machine and return the picked header path."""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception as error:
        raise ValueError(f'Native file browser unavailable: {error}')
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        root.update()
        path = filedialog.askopenfilename(title='Choose local volume header', filetypes=[
                                          ('Volume headers', '*.dat *.nhdr *.nrrd'), ('All files', '*.*')])
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    return path.strip() or None


class Session:
    def __init__(self, source, goal, model='qwen3.5:9b', budget=4):
        self.id = str(uuid.uuid4())
        self.directory = RUNTIME/self.id
        self.directory.mkdir()
        self.goal = goal
        self.source = source
        self.model = model
        self.budget = budget
        self.used = 0
        self.events = []
        self.scene = None
        self.volume = None
        self.result = None
        self.messages = []
        self.state = 'created'
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.save()

    def save(self):
        data = {k: getattr(self, k) for k in [
            'id', 'goal', 'source', 'model', 'budget', 'used', 'state', 'scene', 'volume', 'result', 'events']}
        p = self.directory/'session.tmp'
        p.write_text(json.dumps(data, indent=2))
        # Windows readers (TUI/launcher polling session.json) or AV can hold
        # the destination open, making an atomic replace fail with WinError 5.
        # Retry briefly instead of leaving a stale session.tmp behind.
        for attempt in range(10):
            try:
                p.replace(self.directory/'session.json')
                return
            except OSError:
                if attempt == 9:
                    raise
                time.sleep(0.05)

    def event(self, kind, **data):
        import datetime
        event = {'seq': len(self.events), 'kind': kind, 'time': datetime.datetime.now(
            datetime.timezone.utc).isoformat(), **data}
        self.events.append(event)
        with (self.directory/'events.jsonl').open('a') as f:
            f.write(json.dumps(event)+'\n')
        self.save()

    def run(self, turns=2):
        if not self.lock.acquire(blocking=False):
            raise ValueError('Session already running')
        try:
            self.stop.clear()
            self.state = 'running'
            self.save()
            driver = OllamaDriver(self.model)
            if self.volume is None:
                inventory = driver.inventory()
                (self.directory/'model.json').write_text(json.dumps(inventory, indent=2))
                self.volume = prepare(self.source, self.directory/'volume')
                self.scene = initial_scene(self.volume)
                self.result = render(
                    self.scene, self.volume, self.directory/'frame-0', self.stop)
                self.event('initial_render', result=self.result)
                self.messages = [{'role': 'system', 'content': 'You operate a real volume renderer through these available tools. render_scene: the host WILL execute parameters.tfStart, parameters.tfWidth, parameters.rotationXYZ and parameters.crop on the actual native renderer, and return a newly captured PNG in the next user message. Call render_scene to acquire a new view; you do not need shell or GSC access. finish_exploration: stop without executing any rendering; it cannot produce new evidence. Return exactly one action object matching the supplied schema. Inspect actual attached screenshots, select ONE structured action, or finish honestly. No semantic identities are known. TF smoothstep start/width affects RGB and opacity monotonically: below start is zero; above start+width is opaque/bright. Crop uses half-open storage voxel bounds [x0,x1,y0,y1,z0,z1], NOT normalized coordinates. Camera uses renderer Euler degrees applied X then Y then Z; anatomical directions are unknown. All fields in parameters are required; preserve unrelated current settings. Use sceneRevision exactly. Use finish_exploration when done or uncertain; its parameters must repeat current settings. Nonblack pixels and your confidence are not task correctness. No shell or arbitrary GSC.'}, image_message(self.result, 'User task: '+self.goal+'\nNumeric volume facts: '+json.dumps({
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  k: self.volume[k] for k in ['dimensions', 'geometry', 'orientation', 'rendererMapping']})+'\nChoose next action.')]
            for _ in range(min(turns, self.budget-self.used)):
                if self.stop.is_set():
                    raise InterruptedError('Stopped')
                self.used += 1
                self.save()
                action = driver.decide(
                    self.messages, self.directory/f'call-{self.used}', self.stop)
                self.event('model_action', action=action)
                if action['sceneRevision'] != self.scene['revision']:
                    raise ValueError('Stale model scene revision')
                params = action['parameters']
                candidate = copy.deepcopy(self.scene)
                candidate['tf'].update(
                    start=params['tfStart'], width=params['tfWidth'])
                candidate['camera']['rotationXYZ'] = params['rotationXYZ']
                candidate['crop'] = params['crop']
                validate_scene(candidate, self.volume)
                self.messages.append(
                    {'role': 'assistant', 'content': json.dumps(action)})
                if action['tool'] == 'finish_exploration':
                    # Finish is non-mutating; validated parameter fields are not applied.
                    self.state = 'finished'
                    self.event(
                        'finish', modelClaim=action['reason'], externalTaskVerification='unverified')
                    return
                candidate['revision'] += 1
                result = render(candidate, self.volume,
                                self.directory/f'frame-{self.used}', self.stop)
                # Only verified execution advances the current scene.
                self.scene = candidate
                self.result = result
                self.event('render', result=result)
                self.messages.append(image_message(
                    result, 'This is the actual new screenshot from your action. Compare with prior frames and choose the next action. Remaining total decisions: '+str(self.budget-self.used)))
            self.state = 'budget_exhausted' if self.used >= self.budget else 'paused'
            self.event('pause', remaining=self.budget-self.used)
        except Exception as error:
            self.state = 'stopped' if self.stop.is_set() or isinstance(
                error, InterruptedError) else 'failed'
            self.event('error', message=str(error),
                       traceback=traceback.format_exc())
        finally:
            try:
                (self.directory/'messages.json').write_text(json.dumps(self.messages, indent=2))
            except OSError:
                pass
            try:
                self.save()
            finally:
                self.lock.release()


sessions = {}
external_tools = ExternalTools(Session, sessions)


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, typ='application/json'):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/api/tools':
            return self.send(200, TOOLS)
        if route == '/api/service':
            return self.send(200, {'service': 'gav-native-renderer', 'toolContractVersion': 1})
        if route == '/api/sessions':
            return self.send(200, [{'id': s.id, 'state': s.state} for s in sessions.values()])
        if route == '/api/volume/imports':
            items = []
            try:
                for child in sorted(imports_root().iterdir()):
                    meta = child/'import.json'
                    if meta.is_file():
                        try:
                            data = json.loads(meta.read_text())
                            items.append({'importId': data.get('importId', child.name),
                                          'source': data.get('source', ''),
                                          'datUrl': data.get('datUrl'), 'rawUrl': data.get('rawUrl'),
                                          'dimensions': (data.get('volume') or {}).get('dimensions')})
                        except Exception:
                            continue
            except Exception:
                items = []
            return self.send(200, {'imports': items})
        if route.startswith('/api/volume/import/'):
            try:
                record = get_volume_import(route.rsplit('/', 1)[-1])
                return self.send(200, {'importId': record['importId'], 'source': record['source'],
                                       'volume': record['volume'], 'datUrl': record['datUrl'], 'rawUrl': record['rawUrl']})
            except Exception as error:
                return self.send(404, {'error': str(error)})
        if route.startswith('/api/session/'):
            s = sessions.get(route.split('/')[-1])
            return self.send(200, json.loads((s.directory/'session.json').read_text())) if s else self.send(404, {'error': 'Unknown session'})
        if route.startswith('/api/'):
            return self.send(404, {'error': 'Unknown route'})
        if route.startswith('/artifact/'):
            p = (RUNTIME/unquote(route.removeprefix('/artifact/'))).resolve()
            if RUNTIME.resolve() not in p.parents or not p.is_file():
                return self.send(404, {'error': 'Unknown artifact'})
            return self.send(200, p.read_bytes(), mimetypes.guess_type(p)[0] or 'application/octet-stream')
        if route == '/':
            p = FRONTEND/'index.html'
            if not p.is_file():
                return self.send(503, {'error': 'Frontend build missing; run npm run build in frontend/'})
            return self.send(200, p.read_bytes(), 'text/html; charset=utf-8')
        # Static assets stay inside the built frontend directory; unknown routes never fall back to HTML.
        p = (FRONTEND/unquote(route.lstrip('/'))).resolve()
        if FRONTEND.resolve() not in p.parents or not p.is_file():
            return self.send(404, {'error': 'Unknown route'})
        self.send(200, p.read_bytes(), mimetypes.guess_type(p)
                  [0] or 'application/octet-stream')

    def do_POST(self):
        # Loopback only; reject browser cross-origin mutations and DNS rebinding.
        if self.headers.get('Host') not in [f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}']:
            return self.send(403, {'error': 'Invalid host'})
        if self.headers.get('Origin') not in [None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}']:
            return self.send(403, {'error': 'Cross-origin request rejected'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 32768:
                raise ValueError('Request size invalid')
            body = json.loads(self.rfile.read(length))
            route = urlsplit(self.path).path
            if route == '/api/tools/open':
                return self.send(200, external_tools.open(body))
            if route == '/api/tools/call':
                return self.send(200, external_tools.call(body))
            if route == '/api/start':
                if any(s.lock.locked() for s in sessions.values()):
                    raise ValueError('Another task owns the renderer')
                if not isinstance(body.get('source'), str) or not isinstance(body.get('goal'), str) or not body['goal'].strip():
                    raise ValueError('Source and task required')
                import_id = body.get('importId')
                if import_id is not None and (not isinstance(import_id, str) or not import_id.strip()):
                    raise ValueError('importId must be a non-empty string when provided')
                s = Session(body['source'], body['goal'],
                            body.get('model', 'qwen3.5:9b'))
                if import_id:
                    # Reuse the preview import snapshot: copy the already-converted
                    # render.dat/render.raw/volume.json into the session directory
                    # so the agent run never reconverts and stays on identical bytes.
                    import shutil
                    record = get_volume_import(import_id.strip())
                    s.source = record['source']
                    src_volume = Path(record['directory'])/'volume'
                    dst_volume = s.directory/'volume'
                    if dst_volume.exists():
                        shutil.rmtree(dst_volume)
                    shutil.copytree(src_volume, dst_volume)
                    # Guard against a snapshot that changed on disk after import.
                    snapshot_volume = json.loads((dst_volume/'volume.json').read_text())
                    s.volume = snapshot_volume
                    s.event('volume_reused', importId=record['importId'], source=s.source)
                sessions[s.id] = s
                threading.Thread(target=s.run, daemon=True).start()
                return self.send(202, {'id': s.id})
            if route == '/api/volume/import':
                source = body.get('path', body.get('source', ''))
                record = create_volume_import(source)
                return self.send(200, {'importId': record['importId'], 'source': record['source'],
                                       'volume': record['volume'], 'datUrl': record['datUrl'], 'rawUrl': record['rawUrl']})
            if route == '/api/browse-file':
                picked = pick_volume_file()
                return self.send(200, {'cancelled': picked is None} if picked is None else {'cancelled': False, 'path': picked})
            s = sessions[body['id']]
            if route == '/api/stop':
                s.stop.set()
                return self.send(202, {'state': 'stop_requested'})
            if route == '/api/continue':
                if getattr(s, 'external', False):
                    raise ValueError(
                        'Session belongs to an external agent; continue with its tools')
                if s.state not in ['paused'] or s.used >= s.budget:
                    raise ValueError(
                        'Session cannot continue in current state')
                if any(v.lock.locked() for v in sessions.values()):
                    raise ValueError('Renderer is busy')
                threading.Thread(target=s.run, daemon=True).start()
                return self.send(202, {'state': 'continuing'})
            self.send(404, {'error': 'Unknown action'})
        except Exception as error:
            self.send(400, {'error': str(error)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--source')
    parser.add_argument('--goal')
    parser.add_argument('--model', default='qwen3.5:9b')
    args = parser.parse_args()
    if args.source:
        s = Session(
            args.source, args.goal or 'Inspect the overall volume from two complementary views and compare the actual images.', args.model)
        s.run()
        print(json.dumps({'id': s.id, 'state': s.state,
              'directory': str(s.directory)}))
    else:
        ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()

"""Local harness and thin GUI share the same real executor and model driver."""
import argparse
import json
import copy
import re
import sys
import threading
import time
import uuid
import traceback
import mimetypes
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, unquote, parse_qs
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


def find_import_by_source(source):
    """Reuse an existing import for the same source file instead of converting twice."""
    target = str(Path(source).resolve())
    with VOLUME_IMPORTS_LOCK:
        for record in VOLUME_IMPORTS.values():
            if record.get('source') == target:
                return record
    try:
        children = sorted(imports_root().iterdir())
    except OSError:
        return None
    for child in children:
        meta = child / 'import.json'
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        if str(data.get('source') or '') == target:
            try:
                return get_volume_import(str(data.get('importId') or child.name))
            except ValueError:
                return None
    return None


def materialized_builtin(stem):
    """Path of the runnable file for a built-in preview dataset, or None.

    Built-in datasets ship as QVis **lz4** payloads that the import chain cannot
    read, so ``scripts/materialize_dataset.py`` writes one plain uint8 volume per
    stem into ``volumes/``. Only that materialized file can feed the renderer.
    """
    if not isinstance(stem, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', stem.strip()):
        return None
    matches = sorted((ROOT / 'volumes').glob('%s_*_uint8.dat' % stem.strip()))
    return matches[0] if matches else None


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


# ---- Volume TUI bridge -------------------------------------------------
# The GUI chat is a second skin over the same coding-agent runs the Textual
# TUI drives (scripts/volume_tui + scripts/run_local_agent.py). These routes
# reuse the TUI's pure helpers statelessly: every transcript request rebuilds
# the Transcript from disk (fresh EventTail/StreamTail/Transcript), so the
# GUI and the TUI can never drift apart in wording or marks.
TUI_DIR = ROOT / 'scripts' / 'volume_tui'
for _tui_path in (str(ROOT / 'scripts'), str(TUI_DIR)):
    if _tui_path not in sys.path:
        sys.path.insert(0, _tui_path)
try:
    import transcript as tui_transcript
    import trace_events as tui_trace
    import live_transcript as tui_live_transcript
    import runs as tui_runs
    import chat_log as tui_chat_log
    import export_images as tui_export_images
    import config as tui_config
    import volumes as tui_volumes
    import services as tui_services
    import runner as tui_runner
    import flows as tui_flows
    import evidence as tui_evidence
    import env as tui_env
    import batches as tui_batches
    import event_text as tui_event_text
    TUI_AVAILABLE = True
    TUI_IMPORT_ERROR = ''
except Exception as _tui_import_error:  # pragma: no cover - surfaced via /api/tui/status
    TUI_AVAILABLE = False
    TUI_IMPORT_ERROR = str(_tui_import_error)


def _tui_require():
    if not TUI_AVAILABLE:
        raise ValueError('Volume TUI bridge unavailable: %s' % (TUI_IMPORT_ERROR or 'import failed'))


def _tui_jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _tui_problem_text(problem):
    """One actionable line for a refused start: name, measured detail, fix."""
    if isinstance(problem, str):
        return problem.strip() or 'unknown problem'
    if not isinstance(problem, dict):
        return str(problem)
    parts = [
        str(problem.get('name') or '').strip(),
        str(problem.get('detail') or '').strip(),
        str(problem.get('fix') or '').strip(),
    ]
    return ' — '.join(part for part in parts if part) or 'unknown problem'


def _tui_run_status(run_dir):
    try:
        data = json.loads((Path(run_dir) / 'run.json').read_text(encoding='utf-8'))
        return str((data or {}).get('status') or 'unknown').lower()
    except (OSError, ValueError):
        return 'unknown'


def _tui_run_config(run_dir):
    try:
        data = json.loads((Path(run_dir) / 'config.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _tui_is_live(entry):
    try:
        return bool(tui_runs.is_live(entry))
    except Exception:
        return str(entry.get('status') or '').lower() == 'running'


_tui_transcript_sessions = {}
_tui_transcript_lock = threading.Lock()


def _tui_transcript_payload(run_id):
    """Full conversation of one run: task block + folded evidence blocks.

    One ``LiveTranscript`` per run is kept in this process. Each poll folds
    only the evidence written since the previous poll, so a row that is
    already on screen can never move, disappear or be reported twice when
    the harness and the model stream are re-read.
    """
    _tui_require()
    entry = tui_runs.find_run(run_id, _fresh=True)
    if entry is None:
        raise ValueError('Unknown run')
    run_dir = Path(entry['directory'])
    config = _tui_run_config(run_dir)
    task = str(config.get('task') or '')
    model = str(config.get('model') or '')
    try:
        budget = int(config.get('renderBudget', 4))
    except (TypeError, ValueError):
        budget = 4
    started_at = tui_runs.run_started_at(run_dir)
    with _tui_transcript_lock:
        live = _tui_transcript_sessions.get(run_id)
        if live is None or live.run_dir != run_dir:
            live = tui_live_transcript.LiveTranscript(
                run_dir,
                task,
                started_at,
                tui_trace.resolve_session_dir(run_dir),
            )
            _tui_transcript_sessions[run_id] = live
            # Keep the process bounded; live runs are recreated from disk.
            while len(_tui_transcript_sessions) > 12:
                oldest = min(
                    _tui_transcript_sessions,
                    key=lambda key: _tui_transcript_sessions[key].last_used,
                )
                if oldest == run_id:
                    break
                _tui_transcript_sessions.pop(oldest, None)
    live.poll()
    live.set_task(task)
    state = _tui_run_status(run_dir)
    stopping = (run_dir.parent / 'tui-control' / ('%s.stop' % entry.get('runId'))).is_file()
    live.finalize(state, stopping)
    blocks = live.blocks
    used = sum(1 for b in blocks if b.get('kind') == tui_transcript.TOOL)
    return _tui_jsonable({
        'runId': entry.get('runId'), 'directory': entry.get('directory'),
        'status': state, 'task': task, 'model': model,
        'budget': budget, 'used': used,
        'startedAt': started_at,
        'sessionId': entry.get('sessionId'),
        'volumePath': config.get('volumePath', ''),
        'blocks': blocks,
    })


def _tui_sanitize_run(entry):
    return _tui_jsonable({
        'runId': entry.get('runId'), 'directory': entry.get('directory'),
        'task': entry.get('task'), 'status': entry.get('status'),
        'model': entry.get('model'), 'frames': entry.get('frames'),
        'startedAt': entry.get('startedAt'), 'sessionId': entry.get('sessionId'),
    })


_tui_verify_jobs = {}
_tui_verify_lock = threading.Lock()


def _tui_verify_job_state(run_id):
    with _tui_verify_lock:
        job = _tui_verify_jobs.get(run_id)
        if job is None:
            return {'running': False, 'result': None}
        return {'running': job.get('running', False), 'result': job.get('result')}


def _tui_batch_cancel_flags():
    if not hasattr(_tui_batch_cancel_flags, 'flags'):
        _tui_batch_cancel_flags.flags = {}
    return _tui_batch_cancel_flags.flags


def _tui_launch_batch_member(config_path):
    result = tui_runner.launch(Path(config_path))
    return {'runId': result.run_id,
            'directory': str(result.directory) if result.directory else None,
            'exit_code': result.exit_code, 'error': None,
            'output_tail': (result.output_tail or '')[-2000:]}


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
        if route == '/api/volume/builtins':
            # Which built-in preview datasets also exist as runnable volumes on
            # this machine; anything else is preview-only in the Stage menu.
            stems = []
            try:
                for path in sorted((ROOT / 'volumes').glob('*_*x*x*_uint8.dat')):
                    stem = path.name.split('_')[0]
                    if stem and stem not in stems:
                        stems.append(stem)
            except OSError:
                stems = []
            return self.send(200, {'stems': stems})
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
        if route == '/api/tui/status':
            return self.send(200, {'available': TUI_AVAILABLE, 'error': TUI_IMPORT_ERROR or None})
        if route == '/api/tui/glossary':
            try:
                _tui_require()
                return self.send(200, {'glossary': [{'term': t, 'meaning': m} for t, m in tui_event_text.GLOSSARY]})
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route == '/api/tui/settings':
            try:
                _tui_require()
                return self.send(200, tui_config.load_settings())
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route == '/api/tui/volumes':
            try:
                _tui_require()
                try:
                    selected = tui_config.selected_volume(tui_config.load_settings())
                    selected = str(selected) if selected else None
                except Exception:
                    selected = None
                return self.send(200, {'volumes': _tui_jsonable(tui_volumes.discover_volumes(selected))})
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route == '/api/tui/models':
            try:
                _tui_require()
                query = parse_qs(urlsplit(self.path).query)
                settings = tui_config.load_settings()
                endpoints: list[str] = []
                for key in ('endpoint', 'endpoints'):
                    for raw in query.get(key, []):
                        for part in str(raw).split(','):
                            part = part.strip()
                            if part and part not in endpoints:
                                endpoints.append(part)
                if not endpoints:
                    for key in ('endpoint', 'h200Endpoint'):
                        try:
                            raw = str(settings.get(key) or '').strip()
                        except Exception:
                            raw = ''
                        if raw and raw not in endpoints:
                            endpoints.append(raw)
                names: list[str] = []
                for endpoint in endpoints:
                    try:
                        found = tui_services.list_models(endpoint, timeout=3.0)
                    except Exception:
                        found = []
                    for name in found or []:
                        if name not in names:
                            names.append(name)
                return self.send(200, {'models': sorted(names), 'endpoints': endpoints})
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route == '/api/tui/environment':
            try:
                _tui_require()
                settings = tui_config.load_settings()
                return self.send(200, _tui_jsonable({
                    'settings': settings,
                    'environment': tui_env.check_environment(settings),
                    'modelProblems': tui_services.model_problems(settings),
                }))
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route == '/api/tui/runs':
            try:
                _tui_require()
                return self.send(200, {'runs': [_tui_sanitize_run(e) for e in tui_runs.discover_runs(_fresh=True)]})
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route == '/api/tui/archived':
            try:
                _tui_require()
                return self.send(200, {'runs': [_tui_sanitize_run(e) for e in tui_runs.discover_archived_runs()]})
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route == '/api/tui/batches':
            try:
                _tui_require()
                return self.send(200, {'batches': _tui_jsonable(tui_batches.list_batches())})
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route.startswith('/api/tui/batches/'):
            try:
                _tui_require()
                record = tui_batches.load_batch(route.rsplit('/', 1)[-1])
                if record is None:
                    return self.send(404, {'error': 'Unknown batch'})
                return self.send(200, _tui_jsonable(record))
            except Exception as error:
                return self.send(501, {'error': str(error)})
        if route.startswith('/api/tui/runs/'):
            parts = route.split('/')
            # /api/tui/runs/<id>, /transcript, /chat-log, /json-log, /images, /images.zip, /verify
            if len(parts) >= 5:
                run_id = unquote(parts[4])
                action = unquote(parts[5]) if len(parts) > 5 else ''
                try:
                    _tui_require()
                    entry = tui_runs.find_run(run_id, _fresh=True)
                    if entry is None:
                        return self.send(404, {'error': 'Unknown run'})
                    run_dir = Path(entry['directory'])
                    if action == '' or action == 'transcript':
                        return self.send(200, _tui_transcript_payload(run_id))
                    if action == 'chat-log':
                        payload = _tui_transcript_payload(run_id)
                        text = tui_chat_log.chat_log_text(
                            payload['blocks'], run_id=payload['runId'],
                            model=payload['model'], run_dir=payload['directory'])
                        return self.send(200, text.encode('utf-8'), 'text/plain; charset=utf-8')
                    if action == 'json-log':
                        transcript_events = []  # folded view is the transcript; files carry the evidence
                        doc = tui_runs.run_json_log(run_dir, transcript_events)
                        return self.send(200, _tui_jsonable(doc))
                    if action == 'verify':
                        return self.send(200, _tui_jsonable(_tui_verify_job_state(run_id)))
                    if action == 'images':
                        images = tui_export_images.collect_run_images(run_dir)
                        return self.send(200, {'images': _tui_jsonable(images)})
                    if action == 'images.zip':
                        images = tui_export_images.collect_run_images(run_dir)
                        if not images:
                            return self.send(404, {'error': 'No frame images in this run'})
                        zip_path = tui_export_images.export_run_images_zip(images, run_id=entry.get('runId'))
                        raw = Path(zip_path).read_bytes()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header('Content-Length', str(len(raw)))
                        self.send_header('Content-Disposition',
                                         'attachment; filename="%s"' % Path(zip_path).name)
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                except Exception as error:
                    return self.send(501 if TUI_AVAILABLE else 404, {'error': str(error)})
            return self.send(404, {'error': 'Unknown route'})
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
            if route == '/api/volume/builtin':
                # Built-in preview dataset -> runnable volume. The Stage's
                # Dataset selector drives the run volume through this route, so
                # "selected head" can never silently keep running foot.
                stem = str(body.get('stem') or '').strip()
                path = materialized_builtin(stem)
                if path is None:
                    return self.send(404, {'error': (
                        '%s has no runnable volume on this machine. Materialize it once with '
                        'python scripts/materialize_dataset.py VirtualSampling/Raycaster/Datasets/%s.dat'
                        % (stem or '(no stem)', stem or '<name>'))})
                record = find_import_by_source(path) or create_volume_import(str(path))
                return self.send(200, {'importId': record['importId'], 'source': record['source'],
                                       'volume': record['volume'], 'datUrl': record['datUrl'],
                                       'rawUrl': record['rawUrl']})
            if route == '/api/volume/import':
                source = body.get('path', body.get('source', ''))
                record = create_volume_import(source)
                return self.send(200, {'importId': record['importId'], 'source': record['source'],
                                       'volume': record['volume'], 'datUrl': record['datUrl'], 'rawUrl': record['rawUrl']})
            if route == '/api/browse-file':
                picked = pick_volume_file()
                return self.send(200, {'cancelled': picked is None} if picked is None else {'cancelled': False, 'path': picked})
            if route == '/api/tui/settings':
                try:
                    _tui_require()
                    settings = body.get('settings', body)
                    if not isinstance(settings, dict):
                        raise ValueError('settings must be an object')
                    problems = tui_config.validate_settings(settings)
                    if problems:
                        return self.send(400, {'error': '; '.join(problems[:8])})
                    merged = dict(tui_config.load_settings())
                    merged.update({k: settings[k] for k in tui_config.DEFAULTS if k in settings})
                    tui_config.save_settings(merged)
                    return self.send(200, merged)
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs':
                # Start a new Codex launcher run (same semantics as the TUI composer).
                try:
                    _tui_require()
                    for entry in tui_runs.discover_runs(_fresh=True):
                        if _tui_is_live(entry):
                            raise ValueError('A run is still live (%s) - stop it first' % entry.get('runId'))
                    task_kind = str(body.get('taskKind') or 'custom').strip().lower()
                    if task_kind in ('task-a', 'taska', 'a'):
                        task_kind = 'task-a'
                    elif task_kind in ('task-b', 'taskb', 'b'):
                        task_kind = 'task-b'
                    else:
                        task_kind = 'custom'
                    prompt = str(body.get('task') or '')
                    settings = body.get('settings') or tui_config.load_settings()
                    if not isinstance(settings, dict):
                        raise ValueError('settings must be an object')
                    effective, problems = tui_flows.build_new_run(task_kind, prompt, settings, tui_config)
                    try:
                        gate = tui_flows.service_gate(settings, tui_env, tui_services)
                    except Exception:
                        gate = []
                    problems = list(problems or []) + [
                        _tui_problem_text(p) for p in (gate or [])]
                    if problems:
                        return self.send(400, {'error': '; '.join(problems[:8])})
                    config_path = tui_flows.write_config(dict(effective), task_kind, tui_config)

                    def _launch(path):
                        try:
                            tui_runner.launch(Path(path))
                        except Exception:
                            pass
                    threading.Thread(target=_launch, args=(str(config_path),), daemon=True).start()
                    return self.send(202, {
                        'started': True,
                        'configPath': str(config_path),
                        'task': str((effective or {}).get('task') or prompt),
                    })
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs/stop':
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    if not run_id:
                        raise ValueError('runId required')
                    marker = tui_runner.request_stop(run_id)
                    return self.send(202, {'state': 'stop_requested', 'marker': str(marker)})
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs/verify':
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    if not run_id:
                        raise ValueError('runId required')
                    with _tui_verify_lock:
                        job = _tui_verify_jobs.get(run_id)
                        if job is not None and job.get('running'):
                            return self.send(202, {'started': False, 'running': True})

                        def _verify(rid):
                            try:
                                entry = tui_runs.find_run(rid, _fresh=True)
                                if entry is None:
                                    raise ValueError('Unknown run')
                                result = tui_runs.run_verify(str(entry['directory']))
                                summary = tui_runs.verify_summary(result.get('report'))
                                with _tui_verify_lock:
                                    _tui_verify_jobs[rid] = {'running': False,
                                                             'result': _tui_jsonable({
                                                                 'code': result.get('code'),
                                                                 'summary': summary,
                                                                 'report': result.get('report'),
                                                                 'output': (result.get('output') or '')[-4000:]})}
                            except Exception as error:
                                with _tui_verify_lock:
                                    _tui_verify_jobs[rid] = {'running': False,
                                                             'result': {'code': None, 'summary': [str(error)],
                                                                        'report': None, 'output': ''}}
                        _tui_verify_jobs[run_id] = {'running': True, 'result': None}
                    threading.Thread(target=_verify, args=(run_id,), daemon=True).start()
                    return self.send(202, {'started': True, 'running': True})
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs/export-chat':
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    payload = _tui_transcript_payload(run_id)
                    path = tui_chat_log.export_chat_log(
                        payload['blocks'], run_id=payload['runId'],
                        model=payload['model'], run_dir=payload['directory'])
                    tui_evidence.open_path(str(Path(path).parent))
                    return self.send(200, {'path': str(path)})
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/copy':
                # Server-side clipboard (same machine as the browser here):
                # kind json|chat, built server-side so huge logs never cross HTTP.
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    kind = str(body.get('kind') or 'chat').strip().lower()
                    payload = _tui_transcript_payload(run_id)
                    if kind == 'json':
                        text = json.dumps(tui_runs.run_json_log(
                            Path(payload['directory']), []), indent=2, default=str)
                    else:
                        text = tui_chat_log.chat_log_text(
                            payload['blocks'], run_id=payload['runId'],
                            model=payload['model'], run_dir=payload['directory'])
                    return self.send(200, {'message': tui_evidence.copy_to_clipboard(text)})
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/open-path':
                try:
                    _tui_require()
                    path = str(body.get('path') or '').strip()
                    if not path:
                        raise ValueError('path required')
                    return self.send(200, {'message': tui_evidence.open_path(path)})
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/archive-all':
                try:
                    _tui_require()
                    return self.send(200, _tui_jsonable(tui_runs.archive_all_runs()))
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs/archive':
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    if not run_id:
                        raise ValueError('runId required')
                    return self.send(200, _tui_jsonable(tui_runs.archive_run(run_id)))
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs/unarchive':
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    if not run_id:
                        raise ValueError('runId required')
                    return self.send(200, _tui_jsonable(tui_runs.unarchive_run(run_id)))
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs/rename':
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    title = str(body.get('task', body.get('title', body.get('name', ''))) or '')
                    if not run_id:
                        raise ValueError('runId required')
                    return self.send(200, _tui_jsonable(tui_runs.rename_run(run_id, title)))
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/runs/delete':
                try:
                    _tui_require()
                    run_id = str(body.get('runId') or body.get('id') or '').strip()
                    if not run_id:
                        raise ValueError('runId required')
                    return self.send(200, _tui_jsonable(tui_runs.delete_run(run_id)))
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/delete-all':
                try:
                    _tui_require()
                    return self.send(200, _tui_jsonable(tui_runs.delete_all_runs()))
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/batches':
                # One-shot batch: same frozen config N times (TUI /batch semantics).
                try:
                    _tui_require()
                    for entry in tui_runs.discover_runs(_fresh=True):
                        if _tui_is_live(entry):
                            raise ValueError('A run is still live (%s) - stop it first' % entry.get('runId'))
                    count_raw = body.get('count', body.get('n', 0))
                    task = str(body.get('task') or '')
                    settings = body.get('settings') or tui_config.load_settings()
                    if not isinstance(settings, dict):
                        raise ValueError('settings must be an object')
                    problems = list(tui_batches.validate_batch(count_raw, task) or [])
                    try:
                        problems.extend(tui_config.validate_settings(settings))
                    except Exception:
                        pass
                    if problems:
                        return self.send(400, {'error': '; '.join(problems[:8])})
                    import copy as _copy
                    effective, build_problems = tui_flows.build_new_run('custom', task, dict(settings), tui_config)
                    problems = list(build_problems or [])
                    try:
                        gate = tui_flows.service_gate(dict(settings), tui_env, tui_services)
                        problems.extend([(p if isinstance(p, str) else str(p.get('name') or p.get('detail') or p)) for p in (gate or [])])
                    except Exception:
                        pass
                    if problems:
                        return self.send(400, {'error': '; '.join(problems[:8])})
                    count = tui_batches.parse_count(count_raw)
                    batch_id = tui_batches.new_batch_id()
                    frozen = _copy.deepcopy(dict(effective))
                    tui_batches.create_batch_record(batch_id, frozen, int(count))
                    flags = _tui_batch_cancel_flags()
                    cancel = threading.Event()
                    flags[batch_id] = cancel

                    def _launch_member(seq, frozen_config):
                        import copy as _deepcopy
                        config_path = tui_flows.write_config(_deepcopy.deepcopy(dict(frozen_config)), 'custom', tui_config)
                        return _tui_launch_batch_member(config_path)

                    def _run_batch():
                        try:
                            tui_batches.run_batch_sequential(
                                batch_id, frozen, int(count), _launch_member,
                                should_cancel=cancel.is_set)
                        except Exception:
                            pass
                        finally:
                            flags.pop(batch_id, None)

                    threading.Thread(target=_run_batch, daemon=True).start()
                    return self.send(202, {'started': True, 'batchId': batch_id})
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
            if route == '/api/tui/batches/cancel':
                try:
                    _tui_require()
                    batch_id = str(body.get('batchId') or '').strip()
                    flags = _tui_batch_cancel_flags()
                    flag = flags.get(batch_id)
                    if flag is None:
                        # No live worker: mark remaining items skipped directly.
                        try:
                            record = tui_batches.load_batch(batch_id) or {}
                            items = record.get('items') or []
                            existing = {int(i.get('seq') or 0) for i in items}
                            for rest in range(1, int(record.get('planned') or 0) + 1):
                                if rest not in existing:
                                    items.append({'seq': rest, 'runId': None, 'directory': None,
                                                  'status': 'skipped', 'seconds': 0, 'error': 'cancelled'})
                            record['items'] = items
                            record['status'] = 'interrupted'
                            tui_batches.save_batch(record)
                            return self.send(200, {'cancelled': True})
                        except Exception as error:
                            return self.send(400, {'error': str(error)})
                    flag.set()
                    return self.send(202, {'cancelled': True})
                except Exception as error:
                    return self.send(400 if TUI_AVAILABLE else 501, {'error': str(error)})
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

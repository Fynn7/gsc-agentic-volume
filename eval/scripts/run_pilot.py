"""Run one pilot episode through the existing launcher and normalise its evidence.

The launcher (scripts/run_local_agent.py) owns the model, MCP bridge and raw
artifacts. This script only:
  * freezes the task text (instruction + prompt),
  * enforces a wall-clock limit the launcher does not have,
  * copies the selected final image, parses STATUS/REASON/IMAGE,
  * writes meta.json, claim.json, trace.jsonl and launcher.log.

It never retries a model-level failure. Exit codes: 0 run directory produced,
2 preflight failure, 3 infrastructure failure (no run directory).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_common as pc

DEFAULT_RUNS = pc.EVAL / 'runs' / 'pilot'
CONTROL_DIRECTORY = pc.ROOT / 'runtime' / 'tui-control'
TOOL_NAMES = ('inspect_scene', 'render_scene', 'inspect_slice', 'finish_task')


def today_stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def child_python():
    candidate = pc.ROOT / 'runtime' / 'tui-venv' / 'Scripts' / 'pythonw.exe'
    return str(candidate) if candidate.is_file() else sys.executable


def preflight(args):
    try:
        service = pc.http_json(args.backend, '/api/service', timeout=5)
    except Exception as error:
        raise RuntimeError(
            'Renderer backend unreachable at %s/api/service: %s. '
            'Start it with: npm --prefix frontend run dev:backend' % (args.backend, error))
    if service.get('service') != 'gav-native-renderer':
        raise RuntimeError('Renderer service at %s is not gav-native-renderer: %r' % (args.backend, service))
    try:
        show = pc.http_json(args.ollama, '/api/show', {'model': args.model}, timeout=30)
    except Exception as error:
        raise RuntimeError(
            'Ollama/model lookup failed at %s/api/show (model=%s): %s. '
            'Check that Ollama is running and the model is installed.' % (args.ollama, args.model, error))
    capabilities = show.get('capabilities') or []
    if 'vision' not in capabilities:
        raise RuntimeError('Model %r does not report vision capability: %r' % (args.model, capabilities))
    if show.get('remote_host') or show.get('remote_model') or ':cloud' in args.model:
        raise RuntimeError('Model %r is not a local installed model' % args.model)
    return service, show


def check(args):
    """Probe both services without launching anything; report each one separately."""
    results = []
    try:
        service = pc.http_json(args.backend, '/api/service', timeout=5)
        results.append({'name': 'renderer', 'url': args.backend + '/api/service', 'ok': True,
                        'response': service})
    except Exception as error:
        results.append({'name': 'renderer', 'url': args.backend + '/api/service', 'ok': False,
                        'error': str(error), 'hint': 'npm --prefix frontend run dev:backend'})
    try:
        show = pc.http_json(args.ollama, '/api/show', {'model': args.model}, timeout=30)
        results.append({'name': 'ollama', 'url': args.ollama + '/api/show', 'ok': True,
                        'model': args.model, 'capabilities': show.get('capabilities')})
    except Exception as error:
        results.append({'name': 'ollama', 'url': args.ollama + '/api/show', 'ok': False,
                        'model': args.model, 'error': str(error)})
    print(json.dumps({'event': 'check', 'ok': all(item['ok'] for item in results),
                      'services': results}, indent=2, ensure_ascii=False))
    return 0 if all(item['ok'] for item in results) else 2


def build_config(args, task_dir):
    task_text = pc.compose_task(args.task)
    return {
        'harness': 'codex',
        'model': args.model,
        'endpoint': args.ollama,
        'rendererService': args.backend,
        'contextSize': args.context_size,
        'projectDocumentBytes': 0,
        'temperature': 0.0,
        'think': True,
        'volumePath': str(pc.volume_path(args.task)),
        'renderBudget': 30,
        'task': task_text,
        'resultDirectory': str(task_dir),
        'python': child_python(),
    }, task_text


def reader(stream, prefix, log_handle, state, lock):
    try:
        for line in iter(stream.readline, ''):
            with lock:
                log_handle.write(prefix + line)
                log_handle.flush()
            if prefix != 'OUT ':
                continue
            text = line.strip()
            if not text.startswith('{'):
                continue
            try:
                event = json.loads(text)
            except ValueError:
                continue
            if event.get('event') == 'run_started' and event.get('directory'):
                with lock:
                    state['run_id'] = event.get('runId')
                    state['run_dir'] = Path(event['directory'])
            elif event.get('event') == 'session_opened':
                with lock:
                    state['session_id'] = event.get('id')
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def request_stop(state, grace):
    with_grace = float(grace)
    run_id = state.get('run_id')
    if run_id:
        CONTROL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        (CONTROL_DIRECTORY / ('%s.stop' % run_id)).write_text(
            'written by eval/scripts/run_pilot.py wall-clock limit\n', encoding='utf-8')
    deadline = time.monotonic() + with_grace
    while time.monotonic() < deadline:
        if state['process'].poll() is not None:
            return True
        time.sleep(0.5)
    return state['process'].poll() is not None


def kill_tree(process):
    if process.poll() is not None:
        return
    try:
        subprocess.run(['taskkill', '/pid', str(process.pid), '/T', '/F'],
                       capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def newest_run_directory(task_dir, started):
    candidates = []
    for path in task_dir.glob('run-*'):
        if not path.is_dir():
            continue
        try:
            if path.stat().st_mtime >= started - 5:
                candidates.append(path)
        except OSError:
            continue
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def count_tools(events):
    calls = [event for event in events if event.get('kind') == 'external_tool_call']
    per_tool = Counter(call.get('name') for call in calls)
    errors = [event for event in events if event.get('kind') == 'tool_error']
    finish = [event for event in events if event.get('kind') == 'finish']
    return calls, {name: per_tool.get(name, 0) for name in TOOL_NAMES}, errors, (finish[-1] if finish else None)


def http_problems(run_dir):
    """Transport errors or non-2xx responses recorded by the launcher's proxy."""
    transport = 0
    bad_status = 0
    for path in sorted((run_dir / 'http').glob('http-*.metadata.json')):
        data = pc.read_json(path, {}) or {}
        if data.get('transportError'):
            transport += 1
        status = data.get('status')
        if isinstance(status, int) and status >= 400:
            bad_status += 1
    return transport, bad_status


def build_trace(events, run_id, task, condition):
    lines = []
    step = 0
    pending = None
    for event in events:
        kind = event.get('kind')
        if kind == 'initial_render':
            result = event.get('result') or {}
            scene = result.get('scene') or {}
            lines.append({'run_id': run_id, 'task_id': task, 'condition': condition, 'step': 0,
                          't_start': event.get('time'), 't_end': event.get('time'), 'role': 'tool',
                          'tool_name': 'initial_render', 'tool_args': {}, 'tool_result': None,
                          'screenshot_path': pc.relative(result.get('image')) if result.get('image') else None,
                          'scene_revision': scene.get('revision'), 'tokens_in': None, 'tokens_out': None,
                          'error': None})
            continue
        if kind == 'external_tool_call':
            pending = {'name': event.get('name'), 'arguments': event.get('arguments') or {},
                       't_start': event.get('time'), 'screenshot': None, 'error': None,
                       'summary': {}, 'step': step + 1}
            continue
        if kind in ('render', 'slice'):
            if pending is not None:
                result = event.get('result') or {}
                pending['screenshot'] = result.get('image')
                pending['summary'] = {'imageSha256': result.get('imageSha256'),
                                      'sceneRevision': (result.get('scene') or {}).get('revision'),
                                      'axis': result.get('axis'), 'index': result.get('index')}
            continue
        if kind == 'tool_error':
            if pending is not None:
                pending['error'] = event.get('message')
            else:
                lines.append({'run_id': run_id, 'task_id': task, 'condition': condition,
                              'step': step + 1, 't_start': event.get('time'), 't_end': event.get('time'),
                              'role': 'tool', 'tool_name': None, 'tool_args': {}, 'tool_result': None,
                              'screenshot_path': None, 'scene_revision': None, 'tokens_in': None,
                              'tokens_out': None, 'error': event.get('message')})
                step += 1
            continue
        if kind == 'finish':
            if pending is not None:
                pending['summary']['selectedRevision'] = event.get('selectedRevision')
            continue
        if kind == 'external_tool_return':
            if pending is not None:
                step += 1
                lines.append({'run_id': run_id, 'task_id': task, 'condition': condition,
                              'step': pending['step'], 't_start': pending['t_start'],
                              't_end': event.get('time'), 'role': 'tool',
                              'tool_name': pending['name'], 'tool_args': pending['arguments'],
                              'tool_result': pending['summary'], 'screenshot_path': pending['screenshot'],
                              'scene_revision': pending['summary'].get('sceneRevision'),
                              'tokens_in': None, 'tokens_out': None, 'error': pending['error']})
                pending = None
    return lines


def run(args):
    key, info = pc.task_info(args.task)
    task_text = pc.compose_task(key)
    if args.dry_run:
        task_dir = Path(args.runs_root) / args.condition / key
        config, _ = build_config(args, task_dir)
        config_path = task_dir / '_launch-configs' / ('dryrun-%s-i%02d.json' % (today_stamp(), args.index))
        command = [sys.executable, '-B', str(pc.ROOT / 'scripts' / 'run_local_agent.py'),
                   '--config', str(config_path)]
        print(json.dumps({'dry_run': True, 'task': key, 'condition': args.condition,
                          'index': args.index, 'volume': str(pc.volume_path(key)),
                          'run_base': str(task_dir), 'config_path': str(config_path),
                          'command': command, 'timeout_seconds': args.timeout,
                          'task_text': task_text}, indent=2, ensure_ascii=False))
        return 0

    try:
        service, show = preflight(args)
    except Exception as error:
        print(json.dumps({'event': 'preflight_failed', 'error': str(error)}, ensure_ascii=False))
        return 2

    task_dir = Path(args.runs_root) / args.condition / key
    task_dir.mkdir(parents=True, exist_ok=True)
    stem = 'run-%s-i%02d' % (today_stamp(), args.index)
    config_dir = task_dir / '_launch-configs'
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / (stem + '.json')
    config, _ = build_config(args, task_dir)
    pc.write_json(config_path, config)
    launch_log = task_dir / '_launch-logs' / (stem + '.log')
    launch_log.parent.mkdir(exist_ok=True)

    command = [sys.executable, '-B', str(pc.ROOT / 'scripts' / 'run_local_agent.py'),
               '--config', str(config_path)]
    started_wall = time.time()
    state = {'run_id': None, 'run_dir': None, 'session_id': None, 'process': None}
    lock = threading.Lock()
    timed_out = False
    with open(launch_log, 'w', encoding='utf-8', errors='replace') as log_handle:
        log_handle.write('COMMAND %s\n' % json.dumps(command))
        process = subprocess.Popen(command, cwd=str(pc.ROOT), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   errors='replace', bufsize=1,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        state['process'] = process
        threads = [threading.Thread(target=reader, args=(process.stdout, 'OUT ', log_handle, state, lock), daemon=True),
                   threading.Thread(target=reader, args=(process.stderr, 'ERR ', log_handle, state, lock), daemon=True)]
        for thread in threads:
            thread.start()
        try:
            deadline = time.monotonic() + float(args.timeout)
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    finished = request_stop(state, args.stop_grace)
                    if not finished:
                        kill_tree(process)
                    break
                time.sleep(0.5)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill_tree(process)
                process.wait(timeout=15)
        except KeyboardInterrupt:
            request_stop(state, 10)
            kill_tree(process)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            raise
        finally:
            for thread in threads:
                thread.join(timeout=5)

    run_dir = state.get('run_dir')
    if run_dir is None or not Path(run_dir).is_dir():
        run_dir = newest_run_directory(task_dir, started_wall)
    run_dir = Path(run_dir) if run_dir else None
    ended_wall = time.time()

    if run_dir is None:
        print(json.dumps({'event': 'infra_failed', 'task': key, 'condition': args.condition,
                          'index': args.index, 'exit_code': process.returncode,
                          'timed_out': timed_out, 'launch_log': str(launch_log),
                          'error': 'launcher produced no run directory'}, ensure_ascii=False))
        return 3

    shutil.copy2(launch_log, run_dir / 'launcher.log')
    record = pc.read_json(run_dir / 'run.json', {}) or {}
    manifest = pc.read_json(run_dir / 'manifest.json', {}) or {}
    session_id = record.get('sessionId') or manifest.get('sessionId') or state.get('session_id')
    session_dir = pc.ROOT / 'runtime' / session_id if session_id else None
    session_snapshot = pc.read_json(run_dir / 'session.json', {}) or {}
    live_snapshot = pc.read_json(session_dir / 'session.json', {}) if session_dir else {}
    events = pc.read_events(session_dir) if session_dir else []

    calls, per_tool, errors, finish = count_tools(events)
    used = None
    budget = None
    for snapshot in (live_snapshot or {}, session_snapshot or {}):
        if isinstance(snapshot, dict):
            used = snapshot.get('used', used)
            budget = snapshot.get('budget', budget)

    final_source = None
    if session_dir and (session_dir / 'final' / 'image.png').is_file():
        final_source = session_dir / 'final' / 'image.png'
        shutil.copy2(final_source, run_dir / 'final.png')

    message = pc.last_agent_message(run_dir / 'harness.jsonl')
    claim = pc.parse_claim(message)
    if message:
        (run_dir / 'final_message.txt').write_text(message, encoding='utf-8')
    pc.write_json(run_dir / 'claim.json', dict(claim, raw_final_message=message))

    trace = build_trace(events, run_dir.name, key, args.condition)
    with open(run_dir / 'trace.jsonl', 'w', encoding='utf-8') as handle:
        for line in trace:
            handle.write(json.dumps(line, ensure_ascii=False) + '\n')

    exit_code = process.returncode
    harness_exit = manifest.get('exitCode')
    interrupted = bool(manifest.get('interrupted'))
    transport_errors, http_errors = http_problems(run_dir)
    if timed_out:
        exit_reason = 'wall_clock_timeout'
    elif interrupted:
        exit_reason = 'interrupted'
    elif harness_exit == 0:
        exit_reason = 'completed'
    elif harness_exit is None:
        exit_reason = 'launcher_exit_%s' % exit_code
    else:
        exit_reason = 'harness_exit_%s' % harness_exit
    seconds = manifest.get('seconds')
    if not isinstance(seconds, (int, float)):
        seconds = round(ended_wall - started_wall, 1)
    infra_candidate = len(calls) == 0
    notes = []
    if infra_candidate:
        notes.append('no tool call was observed; check launcher.log before attributing to the model')
    if timed_out:
        notes.append('wall-clock stop requested; harness may have been killed before finish_task')
    if interrupted and not timed_out:
        notes.append('launcher recorded an interruption (stop file or external signal)')
    if transport_errors:
        notes.append('%d inference request(s) ended with a transport error; model service may have died (check http/*.metadata.json)' % transport_errors)
    if http_errors:
        notes.append('%d inference request(s) returned HTTP >= 400' % http_errors)
    meta = {
        'schema': 1,
        'runId': run_dir.name,
        'launcherRunId': record.get('runId'),
        'index': args.index,
        'task': key,
        'condition': args.condition,
        'kind': info['kind'],
        'split': info['split'],
        'model': args.model,
        'harness': 'codex',
        'harnessVersion': manifest.get('harnessVersion'),
        'gitSha': pc.git_sha(),
        'startedAt': record.get('startedAt'),
        'endedAt': manifest.get('ended'),
        'seconds': round(float(seconds), 1),
        'wallClockLimitSeconds': args.timeout,
        'launcherExitCode': exit_code,
        'harnessExitCode': harness_exit,
        'interrupted': interrupted,
        'exitReason': exit_reason,
        'toolCalls': len(calls),
        'perTool': per_tool,
        'toolErrors': len(errors),
        'observationsUsed': used,
        'budget': budget,
        'status': claim['status'],
        'claimBasis': claim['basis'],
        'finalImage': 'final.png' if final_source else None,
        'finalImageSource': pc.relative(final_source) if final_source else None,
        'finishSelectedRevision': finish.get('selectedRevision') if finish else None,
        'sessionId': session_id,
        'sessionDirectory': pc.relative(session_dir) if session_dir else None,
        'launcherRunDirectory': pc.relative(run_dir),
        'configSha256': record.get('configSha256'),
        'promptSha256': pc.sha256_text(pc.prompt(key)),
        'instructionSha256': pc.sha256_text(pc.instruction()),
        'inferenceTransportErrors': transport_errors,
        'inferenceHttpErrors': http_errors,
        'service': service,
        'modelCapabilities': show.get('capabilities'),
        'infraRerun': bool(args.infra_rerun),
        'infraCandidate': infra_candidate,
        'notes': notes,
    }
    pc.write_json(run_dir / 'meta.json', meta)

    print(json.dumps({'event': 'pilot_run', 'task': key, 'condition': args.condition,
                      'index': args.index, 'runId': run_dir.name, 'directory': str(run_dir),
                      'status': claim['status'], 'toolCalls': len(calls),
                      'exitReason': exit_reason, 'finalImage': meta['finalImage'],
                      'infraCandidate': infra_candidate}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run one pilot episode (task x condition x index).')
    parser.add_argument('--task', required=True, help='T1..T7')
    parser.add_argument('--condition', default='local35b_v0')
    parser.add_argument('--index', type=int, default=1, help='repeat number, recorded in meta.json')
    parser.add_argument('--model', default='qwen3.6:35b')
    parser.add_argument('--backend', default='http://127.0.0.1:8765')
    parser.add_argument('--ollama', default='http://127.0.0.1:11434')
    parser.add_argument('--context-size', type=int, default=16384)
    parser.add_argument('--timeout', type=float, default=1800.0, help='wall-clock seconds')
    parser.add_argument('--stop-grace', type=float, default=90.0,
                        help='seconds to wait after the cooperative stop file before killing the tree')
    parser.add_argument('--runs-root', default=str(DEFAULT_RUNS))
    parser.add_argument('--infra-rerun', action='store_true', help='mark this run as an infra retry')
    parser.add_argument('--check', action='store_true', help='probe renderer and Ollama, then exit')
    parser.add_argument('--dry-run', action='store_true', help='print paths and command only')
    args = parser.parse_args(argv)
    try:
        if args.check:
            return check(args)
        return run(args)
    except KeyboardInterrupt:
        print(json.dumps({'event': 'interrupted'}, ensure_ascii=False))
        return 130


if __name__ == '__main__':
    sys.exit(main())

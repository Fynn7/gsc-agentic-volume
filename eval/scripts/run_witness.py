"""Produce a witness solution through the real tool contract (no model).

The witness is the Gate-1 artefact: a fixed sequence of MCP-equivalent tool
calls that reaches the target result. Calls go through the backend's
/api/tools/open and /api/tools/call endpoints, which are the same contract the
MCP adapter exposes (scripts/renderer_mcp.py).

Outputs (default eval/tasks/<task>/):
  witness_calls.json   exact arguments + scalar results + image hash checks
  witness.png          selected native frame
  witness.gsc          the native script that produced it
  witness_scene.json   scene bound to that frame
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_common as pc

PLANS = {
    'T4': {
        'volume': 'volumes/aneurism_256x256x256_uint8.dat',
        'calls': [
            {'name': 'inspect_scene', 'arguments': {}},
            {'name': 'finish_task', 'arguments': {
                'frameRevision': 0,
                'summary': 'Witness solution produced without a model: open the aneurism volume, '
                           'wait for the initial native raycast, inspect the returned PNG, and save frame 0.'}},
        ],
    },
}


def fetch_image(backend, url, expected_sha):
    request = urllib.request.Request(backend.rstrip('/') + url)
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha:
        raise ValueError('Returned image hash mismatch: %s != %s' % (actual, expected_sha))
    return data, actual


def scalar_result(result):
    """Drop base64/paths we do not need, keep the auditable numbers."""
    keep = {}
    for key in ('id', 'state', 'goal', 'remainingObservations', 'imageSha256', 'imageKind',
                'selectedRevision', 'finalDirectory', 'taskSuccess', 'error'):
        if key in result:
            keep[key] = result[key]
    if isinstance(result.get('scene'), dict):
        keep['scene'] = {
            'revision': result['scene'].get('revision'),
            'tf': result['scene'].get('tf'),
            'camera': result['scene'].get('camera'),
            'crop': result['scene'].get('crop'),
        }
    if isinstance(result.get('volume'), dict):
        keep['volume'] = {name: result['volume'].get(name) for name in
                          ('dimensions', 'orientation', 'axisOrder', 'rendererMapping', 'spacingDefaulted')}
    return keep


def run(args):
    key, info = pc.task_info(args.task)
    plan = pc.read_json(args.plan, None) if args.plan else PLANS.get(key)
    if not plan:
        print(json.dumps({'error': 'No witness plan for %s; pass --plan file.json' % key}))
        return 2
    volume = (pc.ROOT / plan['volume']) if not Path(plan['volume']).is_absolute() else Path(plan['volume'])
    out_dir = Path(args.out) if args.out else pc.EVAL / 'tasks' / key
    if not volume.is_file():
        print(json.dumps({'error': 'volume missing: %s' % volume}))
        return 2
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'task': key, 'volume': str(volume),
                          'out_dir': str(out_dir), 'backend': args.backend,
                          'calls': plan['calls']}, indent=2, ensure_ascii=False))
        return 0

    started = time.time()
    record = {'task': key, 'volume': pc.relative(volume), 'backend': args.backend,
              'started': started, 'calls': [], 'contract': 'backend /api/tools/open + /api/tools/call '
              '(same tool contract as scripts/renderer_mcp.py)', 'model': None}
    session = pc.http_json(args.backend, '/api/tools/open', {
        'source': str(volume), 'goal': 'Witness for %s: %s' % (key, pc.prompt(key)),
        'model': 'witness-script', 'budget': args.budget}, timeout=args.open_timeout)
    session_id = session['id']
    record['sessionId'] = session_id
    record['service'] = pc.http_json(args.backend, '/api/service', timeout=5)
    record['initial'] = scalar_result(session)
    if session.get('imageUrl'):
        _, digest = fetch_image(args.backend, session['imageUrl'], session['imageSha256'])
        record['initial']['imageVerified'] = True
        record['initial']['imageSha256'] = digest

    timeout = args.call_timeout
    result = session
    try:
        for call in plan['calls']:
            arguments = dict(call.get('arguments') or {})
            if arguments.get('expectedRevision') == 'current':
                arguments['expectedRevision'] = (result.get('scene') or {}).get('revision')
            started_call = time.time()
            result = pc.http_json(args.backend, '/api/tools/call', {
                'id': session_id, 'name': call['name'], 'arguments': arguments}, timeout=timeout)
            entry = {'name': call['name'], 'arguments': arguments,
                     'seconds': round(time.time() - started_call, 3),
                     'result': scalar_result(result)}
            if result.get('imageUrl'):
                data, digest = fetch_image(args.backend, result['imageUrl'], result['imageSha256'])
                entry['imageVerified'] = True
                entry['imageBytes'] = len(data)
                entry['imageSha256'] = digest
            record['calls'].append(entry)
    except Exception as error:
        record['error'] = str(error)
        record['ended'] = time.time()
        pc.write_json(out_dir / 'witness_calls.json', record)
        print(json.dumps({'event': 'witness_failed', 'task': key, 'error': str(error),
                          'sessionId': session_id, 'calls': len(record['calls'])}, ensure_ascii=False))
        return 1

    final_directory = result.get('finalDirectory')
    if final_directory and Path(final_directory).is_dir():
        source = Path(final_directory)
    else:
        source = pc.ROOT / 'runtime' / session_id / 'frame-0'
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = {}
    for name, target in (('image.png', 'witness.png'), ('script.gsc', 'witness.gsc'),
                         ('scene.json', 'witness_scene.json'), ('native-state.json', 'witness_native_state.json')):
        origin = source / name
        if origin.is_file():
            shutil.copy2(origin, out_dir / target)
            copied[target] = pc.relative(out_dir / target)
    record['finalDirectory'] = pc.relative(source)
    record['copied'] = copied
    record['ended'] = time.time()
    record['seconds'] = round(record['ended'] - started, 1)
    image_calls = [call for call in record['calls'] if 'imageVerified' in call]
    record['imageVerified'] = bool(image_calls) and all(call['imageVerified'] for call in image_calls)
    pc.write_json(out_dir / 'witness_calls.json', record)
    print(json.dumps({'event': 'witness_done', 'task': key, 'sessionId': session_id,
                      'finalDirectory': record['finalDirectory'], 'copied': copied,
                      'imageVerified': record['imageVerified']},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write a fixed-call witness solution.')
    parser.add_argument('--task', required=True, help='T1..T7 (default plan exists for T4)')
    parser.add_argument('--plan', help='JSON file with {volume, calls:[{name, arguments}]}')
    parser.add_argument('--out', help='output directory (default eval/tasks/<task>)')
    parser.add_argument('--backend', default='http://127.0.0.1:8765')
    parser.add_argument('--budget', type=int, default=30)
    parser.add_argument('--open-timeout', type=float, default=600.0)
    parser.add_argument('--call-timeout', type=float, default=180.0)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        pc.task_info(args.task)
        return run(args)
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())

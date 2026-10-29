"""Sweep tfStart on one dataset and build a labelled contact sheet.

Renderer-only: no model is called. Used to pick a T1 witness threshold and to
have calibration images for the acceptance checklist.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_common as pc

DEFAULT_VOLUME = {'T1': 'volumes/foot.dat', 'T2': 'volumes/foot.dat', 'T4': 'volumes/aneurism_256x256x256_uint8.dat'}


def values(start, stop, step):
    result = []
    value = start
    while value <= stop + 1e-9:
        result.append(round(value, 4))
        value += step
    return result


def download(url, path):
    with urllib.request.urlopen(url, timeout=60) as response:
        path.write_bytes(response.read())


def contact_sheet(entries, out_path, columns=5, cell=180, label_height=24):
    from PIL import Image, ImageDraw
    rows = (len(entries) + columns - 1) // columns
    width = columns * cell
    height = rows * (cell + label_height)
    sheet = Image.new('RGB', (width, height), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for index, entry in enumerate(entries):
        row, column = divmod(index, columns)
        x = column * cell
        y = row * (cell + label_height)
        if entry.get('image') and Path(entry['image']).is_file():
            with Image.open(entry['image']) as image:
                image = image.convert('RGB')
                image.thumbnail((cell - 8, cell - 8))
                sheet.paste(image, (x + (cell - image.width) // 2, y + (cell - image.height) // 2))
        else:
            draw.rectangle([x + 4, y + 4, x + cell - 4, y + cell - 4], outline=(120, 60, 60))
            draw.text((x + 8, y + 8), 'render failed', fill=(220, 120, 120))
        label = 'tfStart=%s' % entry.get('tfStart')
        if entry.get('error'):
            label += ' ERROR'
        draw.text((x + 6, y + cell + 4), label, fill=(230, 230, 230))
    sheet.save(out_path)


def run(args):
    key, _ = pc.task_info(args.task)
    volume = pc.ROOT / (args.volume or DEFAULT_VOLUME.get(key, ''))
    if not args.volume and key not in DEFAULT_VOLUME:
        print(json.dumps({'error': 'Pass --volume for %s' % key}))
        return 2
    if not volume.is_file():
        print(json.dumps({'error': 'volume missing: %s' % volume}))
        return 2
    out_dir = Path(args.out) if args.out else pc.EVAL / 'tasks' / key
    sweep_dir = out_dir / 'sweep'
    sweep_values = values(args.start, args.stop, args.step)[:args.max_frames]
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'task': key, 'volume': str(volume),
                          'values': sweep_values, 'tfWidth': args.width,
                          'out': str(out_dir / 'sweep_contact_sheet.png')}, indent=2))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    sweep_dir.mkdir(exist_ok=True)
    session = pc.http_json(args.backend, '/api/tools/open', {
        'source': str(volume), 'goal': 'TF sweep for %s (renderer only)' % key,
        'model': 'sweep-script', 'budget': min(30, max(2, len(sweep_values) + 1))}, timeout=600)
    session_id = session['id']
    revision = (session.get('scene') or {}).get('revision', 0)
    entries = []
    for value in sweep_values:
        started = time.time()
        try:
            result = pc.http_json(args.backend, '/api/tools/call', {
                'id': session_id, 'name': 'render_scene',
                'arguments': {'expectedRevision': revision, 'reason': 'tf sweep %.3f' % value,
                              'parameters': {'tfStart': value, 'tfWidth': args.width}}}, timeout=180)
            image_path = sweep_dir / ('tf_%05.3f.png' % value)
            download(args.backend.rstrip('/') + result['imageUrl'], image_path)
            revision = (result.get('scene') or {}).get('revision', revision)
            entry = {'tfStart': value, 'tfWidth': args.width, 'image': str(image_path),
                     'imageSha256': result.get('imageSha256'), 'revision': revision,
                     'seconds': round(time.time() - started, 2)}
        except Exception as error:
            entry = {'tfStart': value, 'tfWidth': args.width, 'image': None,
                     'error': str(error), 'seconds': round(time.time() - started, 2)}
        entries.append(entry)
        print(json.dumps({'event': 'sweep_frame', **entry}, ensure_ascii=False))
    sheet = out_dir / 'sweep_contact_sheet.png'
    contact_sheet(entries, sheet)
    pc.write_json(out_dir / 'sweep.json', {'task': key, 'sessionId': session_id,
                                           'volume': pc.relative(volume), 'entries': entries,
                                           'contactSheet': pc.relative(sheet),
                                           'note': 'renderer-only sweep; not a model run'})
    print(json.dumps({'event': 'sweep_done', 'task': key, 'sessionId': session_id,
                      'frames': len(entries), 'contactSheet': pc.relative(sheet)}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Sweep tfStart and build a contact sheet (no model).')
    parser.add_argument('--task', default='T1')
    parser.add_argument('--volume', help='override repo-relative volume header')
    parser.add_argument('--start', type=float, default=0.08)
    parser.add_argument('--stop', type=float, default=0.60)
    parser.add_argument('--step', type=float, default=0.04)
    parser.add_argument('--width', type=float, default=0.08)
    parser.add_argument('--max-frames', type=int, default=20)
    parser.add_argument('--out', help='output directory (default eval/tasks/<task>)')
    parser.add_argument('--backend', default='http://127.0.0.1:8765')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    return run(args)


if __name__ == '__main__':
    sys.exit(main())

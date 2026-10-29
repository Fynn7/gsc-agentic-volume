"""Run the overnight pilot matrix in rounds, with infra-only retries.

Order is round-major (T4, T1, T5, T4, T1, T5, ...) so an interruption still
leaves at least one run of every task. Model-level failures are never retried.
Infrastructure failures (no run directory, or a launcher exit before the first
tool call) are retried once with infra_rerun=true; three consecutive infra
failures abort the batch.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_common as pc

DEFAULT_RUNS = pc.EVAL / 'runs' / 'pilot'


def existing_meta(runs_root, condition, task, index):
    task_dir = Path(runs_root) / condition / task
    for path in sorted(task_dir.glob('run-*/meta.json')):
        meta = pc.read_json(path, {}) or {}
        if meta.get('index') == index and not meta.get('infraCandidate') and not meta.get('excluded'):
            return meta
    return None


def invoke(args, task, index, infra_rerun):
    command = [sys.executable, '-B', str(Path(__file__).resolve().parent / 'run_pilot.py'),
               '--task', task, '--condition', args.condition, '--index', str(index),
               '--model', args.model, '--backend', args.backend, '--ollama', args.ollama,
               '--timeout', str(args.timeout), '--runs-root', str(args.runs_root)]
    if infra_rerun:
        command.append('--infra-rerun')
    log_path = Path(args.runs_root) / args.condition / '_batch' / ('batch-%s.log' % time.strftime('%Y%m%d-%H%M%S'))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as log:
        log.write('COMMAND %s\n' % json.dumps(command))
        completed = subprocess.run(command, cwd=str(pc.ROOT), capture_output=True, text=True,
                                   encoding='utf-8', errors='replace')
        log.write('STDOUT\n%s\nSTDERR\n%s\n' % (completed.stdout, completed.stderr))
    summary = None
    for line in reversed((completed.stdout or '').splitlines()):
        line = line.strip()
        if line.startswith('{') and '"event"' in line:
            try:
                summary = json.loads(line)
                break
            except ValueError:
                continue
    return completed.returncode, summary


def run(args):
    tasks = [task.upper() for task in args.tasks]
    for task in tasks:
        pc.task_info(task)
    plan = [(round_index, task) for round_index in range(1, args.rounds + 1) for task in tasks]
    if args.dry_run:
        for index, task in plan:
            print('%s index=%02d condition=%s timeout=%ss' % (task, index, args.condition, int(args.timeout)))
        print('cloud_v0 is not wired into the current launcher; skipped.')
        return 0

    consecutive_infra = 0
    results = []
    for index, task in plan:
        if args.skip_existing:
            meta = existing_meta(args.runs_root, args.condition, task, index)
            if meta:
                print(json.dumps({'event': 'pilot_skipped', 'task': task, 'index': index,
                                  'runId': meta.get('runId')}, ensure_ascii=False))
                results.append(meta)
                continue
        code, summary = invoke(args, task, index, False)
        infra = code in (2, 3) or (summary or {}).get('infraCandidate')
        if infra:
            print(json.dumps({'event': 'infra_retry', 'task': task, 'index': index,
                              'exitCode': code}, ensure_ascii=False))
            time.sleep(5)
            code, summary = invoke(args, task, index, True)
            infra = code in (2, 3) or (summary or {}).get('infraCandidate')
        if infra:
            consecutive_infra += 1
        else:
            consecutive_infra = 0
        if summary:
            results.append(summary)
        print(json.dumps({'event': 'batch_step', 'task': task, 'index': index,
                          'exitCode': code, 'infra': bool(infra),
                          'consecutiveInfra': consecutive_infra,
                          'summary': summary}, ensure_ascii=False))
        if consecutive_infra >= 3:
            print(json.dumps({'event': 'batch_aborted', 'reason': 'three consecutive infra failures'},
                             ensure_ascii=False))
            return 4
    print(json.dumps({'event': 'batch_done', 'runs': len(results), 'condition': args.condition},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the pilot matrix in rounds.')
    parser.add_argument('--condition', default='local35b_v0')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--tasks', nargs='+', default=['T4', 'T1', 'T5'])
    parser.add_argument('--model', default='qwen3.6:35b')
    parser.add_argument('--backend', default='http://127.0.0.1:8765')
    parser.add_argument('--ollama', default='http://127.0.0.1:11434')
    parser.add_argument('--timeout', type=float, default=1800.0)
    parser.add_argument('--runs-root', default=str(DEFAULT_RUNS))
    parser.add_argument('--skip-existing', action='store_true', default=True,
                        help='resume: skip a condition/task/index that already has a non-infra meta.json')
    parser.add_argument('--no-skip-existing', dest='skip_existing', action='store_false')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print(json.dumps({'event': 'batch_interrupted'}, ensure_ascii=False))
        return 130


if __name__ == '__main__':
    sys.exit(main())

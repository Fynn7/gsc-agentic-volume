"""Aggregate pilot runs into the markdown table, CSV and montage.

Rows between <!-- RUNS:BEGIN --> / <!-- RUNS:END --> are regenerated. Existing
values in the last two columns (评分 / 归因) are preserved by run id, so the
human can fill them and re-run this script safely.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_common as pc

RUNS_BEGIN = '<!-- RUNS:BEGIN -->'
RUNS_END = '<!-- RUNS:END -->'
ISSUES_BEGIN = '<!-- ISSUES:BEGIN -->'
ISSUES_END = '<!-- ISSUES:END -->'
HEADER = ['run_id', '任务', '条件', '工具调用数', '墙钟(min)', '错误数', 'STATUS',
          'REASON（缩略）', '最终图', '退出原因', '评分', '归因']


def find_runs(runs_root):
    runs = []
    for path in sorted(Path(runs_root).rglob('meta.json')):
        if '_launch-configs' in path.parts or '_launch-logs' in path.parts:
            continue
        meta = pc.read_json(path, None)
        if not isinstance(meta, dict) or 'runId' not in meta:
            continue
        meta['_directory'] = path.parent
        runs.append(meta)
    runs.sort(key=lambda meta: (str(meta.get('condition')), str(meta.get('task')),
                                int(meta.get('index') or 0), str(meta.get('runId'))))
    return runs


def existing_manual(doc_path):
    manual = {}
    try:
        text = Path(doc_path).read_text(encoding='utf-8')
    except OSError:
        return manual
    if RUNS_BEGIN not in text or RUNS_END not in text:
        return manual
    block = text.split(RUNS_BEGIN, 1)[1].split(RUNS_END, 1)[0]
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith('|') or set(line.replace('|', '').strip()) <= set('-: '):
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if len(cells) < len(HEADER) or cells[0] == 'run_id':
            continue
        manual[cells[0].strip('`')] = (cells[-2], cells[-1])
    return manual


def esc(text, limit=None):
    value = ' '.join(str(text or '').split()).replace('|', '\\|')
    if limit and len(value) > limit:
        value = value[:limit - 3] + '...'
    return value


def table_for(runs, manual):
    lines = ['| ' + ' | '.join(HEADER) + ' |',
             '|' + '|'.join(['---'] * len(HEADER)) + '|']
    for meta in runs:
        run_id = str(meta.get('runId'))
        claim = pc.read_json(meta['_directory'] / 'claim.json', {}) or {}
        status = claim.get('status') or meta.get('status') or 'NONE'
        reason = claim.get('reason') or ''
        final = meta.get('finalImage')
        final_path = (meta['_directory'] / final) if final else None
        final_display = pc.relative(final_path) if final_path and final_path.is_file() else '—'
        score, attribution = manual.get(run_id, ('', ''))
        seconds = meta.get('seconds')
        minutes = ('%.1f' % (float(seconds) / 60.0)) if isinstance(seconds, (int, float)) else '—'
        cells = [('`%s`' % run_id), esc(meta.get('task')), esc(meta.get('condition')),
                 str(meta.get('toolCalls', '—')), minutes, str(meta.get('toolErrors', '—')),
                 esc(status), esc(reason, 60),
                 ('`%s`' % final_display) if final_display != '—' else '—',
                 esc(meta.get('exitReason')), score, attribution]
        lines.append('| ' + ' | '.join(cells) + ' |')
    if not runs:
        lines.append('| — | — | — | — | — | — | — | 尚无 run | — | — | — | — |')
    return '\n'.join(lines)


def issues_for(runs):
    lines = []
    for meta in runs:
        run_id = str(meta.get('runId'))
        exit_code = meta.get('harnessExitCode')
        if exit_code is None:
            exit_code = meta.get('launcherExitCode')
        if meta.get('infraCandidate'):
            lines.append('- `%s`：未观察到任何工具调用（exit=%s，%s）。先看 `launcher.log`，不要归因给模型。'
                         % (run_id, exit_code, meta.get('exitReason')))
        if meta.get('inferenceTransportErrors'):
            lines.append('- `%s`：%s 次推理请求传输中断（可能是模型服务 OOM/崩溃）；看 `http/*.metadata.json`。'
                         % (run_id, meta.get('inferenceTransportErrors')))
        if meta.get('inferenceHttpErrors'):
            lines.append('- `%s`：%s 次推理请求返回 HTTP >= 400。' % (run_id, meta.get('inferenceHttpErrors')))
        if meta.get('exitReason') == 'wall_clock_timeout':
            lines.append('- `%s`：墙钟到点被 run_pilot 停止，可能有帧但未必有 finish_task。' % run_id)
        if meta.get('status') == 'NONE':
            lines.append('- `%s`：终局消息没有可解析的 STATUS/REASON/IMAGE（claimBasis=missing）。' % run_id)
        if not meta.get('finalImage'):
            lines.append('- `%s`：没有选中最终图（session `final/image.png` 不存在）。' % run_id)
        if meta.get('toolErrors'):
            lines.append('- `%s`：工具错误 %s 次；明细见 `trace.jsonl` 的 error 字段。'
                         % (run_id, meta.get('toolErrors')))
    if not lines:
        lines.append('- 暂无可自动识别的问题；人工补充写在本节下方。')
    return '\n'.join(lines)


def replace_block(text, begin, end, body):
    if begin not in text or end not in text:
        return text + '\n\n' + begin + '\n' + body + '\n' + end + '\n'
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return head + begin + '\n' + body + '\n' + end + tail


def montage(runs, out_path, columns=3, cell=260, label_height=52):
    try:
        from PIL import Image, ImageDraw
    except Exception as error:
        out_path.with_suffix('.html').write_text(
            '<html><body><h1>pilot montage</h1>%s</body></html>' % ''.join(
                '<figure><img src="%s"><figcaption>%s</figcaption></figure>' % (
                    (meta['_directory'] / 'final.png').as_uri() if meta.get('finalImage') else '',
                    meta.get('runId')) for meta in runs), encoding='utf-8')
        return 'html (PIL unavailable: %s)' % error
    rows = max(1, (len(runs) + columns - 1) // columns)
    sheet = Image.new('RGB', (columns * cell, rows * (cell + label_height)), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    if not runs:
        draw.text((12, 12), 'no pilot runs yet', fill=(220, 220, 220))
    for index, meta in enumerate(runs):
        row, column = divmod(index, columns)
        x = column * cell
        y = row * (cell + label_height)
        final = meta['_directory'] / 'final.png'
        if meta.get('finalImage') and final.is_file():
            with Image.open(final) as image:
                image = image.convert('RGB')
                image.thumbnail((cell - 10, cell - 10))
                sheet.paste(image, (x + (cell - image.width) // 2, y + (cell - image.height) // 2))
        else:
            draw.rectangle([x + 6, y + 6, x + cell - 6, y + cell - 6], outline=(120, 60, 60))
            draw.text((x + 12, y + 12), 'NO FINAL IMAGE', fill=(230, 120, 120))
        draw.text((x + 6, y + cell + 4), '%s  %s' % (meta.get('task'), meta.get('condition')),
                  fill=(235, 235, 235))
        draw.text((x + 6, y + cell + 18), str(meta.get('runId'))[-28:], fill=(170, 170, 170))
        draw.text((x + 6, y + cell + 32), 'STATUS: %s   calls=%s' % (meta.get('status'), meta.get('toolCalls')),
                  fill=(150, 210, 150) if meta.get('status') == 'DONE' else (230, 200, 120))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return 'png'


def write_csv(runs, path, manual):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['run_id', 'task', 'condition', 'index', 'status', 'claim_basis', 'reason',
                         'tool_calls', 'seconds', 'tool_errors', 'exit_reason', 'tool_errors_detail',
                         'final_image', 'score', 'attribution'])
        for meta in runs:
            run_id = str(meta.get('runId'))
            claim = pc.read_json(meta['_directory'] / 'claim.json', {}) or {}
            score, attribution = manual.get(run_id, ('', ''))
            final = meta.get('finalImage')
            final_path = (meta['_directory'] / final) if final else None
            writer.writerow([run_id, meta.get('task'), meta.get('condition'), meta.get('index'),
                             claim.get('status') or meta.get('status'), meta.get('claimBasis'),
                             claim.get('reason') or '', meta.get('toolCalls'),
                             meta.get('seconds'), meta.get('toolErrors'), meta.get('exitReason'),
                             meta.get('perTool'), pc.relative(final_path) if final_path and final_path.is_file() else '',
                             score, attribution])


def excluded_notes(runs):
    lines = []
    for meta in runs:
        lines.append('- `%s`：已排除 — %s' % (meta.get('runId'), meta.get('exclusionReason') or 'infra contamination'))
    return '\n'.join(lines)


def run(args):
    all_runs = find_runs(args.runs_root)
    excluded = [meta for meta in all_runs if meta.get('excluded')]
    runs = [meta for meta in all_runs if not meta.get('excluded')]
    doc_path = Path(args.doc)
    manual = existing_manual(doc_path)
    try:
        text = doc_path.read_text(encoding='utf-8')
    except OSError:
        text = '# Pilot v0\n\n'
    text = replace_block(text, RUNS_BEGIN, RUNS_END, table_for(runs, manual))
    issues = issues_for(runs)
    if excluded:
        issues += '\n' + excluded_notes(excluded)
    text = replace_block(text, ISSUES_BEGIN, ISSUES_END, issues)
    doc_path.write_text(text, encoding='utf-8')
    write_csv(runs, Path(args.csv), manual)
    kind = montage(runs, Path(args.montage))
    print(json.dumps({'event': 'collected', 'runs': len(runs), 'excluded': len(excluded),
                      'doc': str(doc_path), 'csv': args.csv, 'montage': args.montage,
                      'montage_kind': kind}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Aggregate pilot runs into the doc, CSV and montage.')
    parser.add_argument('--runs-root', default=str(pc.EVAL / 'runs' / 'pilot'))
    parser.add_argument('--doc', default=str(pc.EVAL / 'results' / 'pilot_v0.md'))
    parser.add_argument('--csv', default=str(pc.EVAL / 'results' / 'pilot_v0_runs.csv'))
    parser.add_argument('--montage', default=str(pc.EVAL / 'results' / 'pilot_v0_montage.png'))
    args = parser.parse_args(argv)
    return run(args)


if __name__ == '__main__':
    sys.exit(main())

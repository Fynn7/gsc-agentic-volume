"""Recover owned, unused failed startups; never restart a Codex task or touch daily config."""
import argparse
import ctypes
from ctypes import wintypes as w
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
from desktop_environment import ROOT, ACTIVE, write_json
from desktop_isolation import profile_processes, protected_changes
from windows_profile_probe import normalize, within


def process_info(pid):
    pid=int(pid)
    if pid<=0:raise ValueError('Invalid PID')
    code='[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); '+f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}"'+" | ForEach-Object { [PSCustomObject]@{pid=$_.ProcessId;executable=$_.ExecutablePath;command=$_.CommandLine;created=$_.CreationDate.ToUniversalTime().ToString('o')} } | ConvertTo-Json -Compress"
    raw=subprocess.check_output(['powershell.exe','-NoProfile','-Command',code],text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,timeout=15).strip()
    return json.loads(raw) if raw else None


def created_epoch(row):return datetime.datetime.fromisoformat(row['created'].replace('Z','+00:00')).timestamp()


def terminate_verified(row):
    """Hold a handle and check process creation time, guarding against PID reuse."""
    if row['pid']==os.getpid():raise ValueError('Cannot terminate the recovery process')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.GetProcessTimes.argtypes=[w.HANDLE,ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME)]
    kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT];kernel.TerminateProcess.restype=w.BOOL
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x1000|0x100000|1,False,row['pid'])
    if not handle:
        if process_info(row['pid']) is None:return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        times=[w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):raise ctypes.WinError(ctypes.get_last_error())
        actual=((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)/10000000-11644473600
        if abs(actual-created_epoch(row))>.01:raise ValueError('PID was reused; termination refused')
        if not kernel.TerminateProcess(handle,1):raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(handle,5000)!=0:raise ValueError('Owned process did not exit')
    finally:kernel.CloseHandle(handle)


def owned_python(pid,config,script,marker,started=None):
    row=process_info(pid)
    if row is None:return None
    command=(row.get('command') or '').replace('\\','/').lower()
    expected=str((ROOT/script).resolve()).replace('\\','/').lower()
    if normalize(row.get('executable') or '__missing__')!=normalize(config['python']) or expected not in command or marker.replace('\\','/').lower() not in command:
        raise ValueError('PID ownership mismatch; refusing to stop '+str(pid))
    if started is not None and not started-60<=created_epoch(row)<=started+2:
        raise ValueError('Process creation time does not match this run; refusing to stop '+str(pid))
    return row


def service_status(config):
    base=config['recordingProxy']
    if urlsplit(base).hostname not in ('127.0.0.1','localhost'):raise ValueError('Recovery requires a loopback service')
    try:
        with urllib.request.urlopen(base+'/desktop/status',timeout=2) as response:return json.load(response)
    except (OSError,ValueError):return None


def recover(config, failed_attempt=False, reason='next-start recovery'):
    run=Path(config['resultDirectory']).resolve();base=(ROOT/'runtime/desktop/runs').resolve()
    if not within(run,base) or run==base:raise ValueError('Run directory is outside this repository runtime')
    current=json.loads((run/'config.json').read_text(encoding='utf-8-sig'))
    if current['resultDirectory']!=config['resultDirectory'] or current['formalHarness']!='Codex Desktop':raise ValueError('Wrong run identity')
    status=service_status(config)
    if status and (normalize(status.get('resultDirectory','__missing__'))!=normalize(run) or status.get('formalHarness')!='Codex Desktop'):
        raise ValueError('Recording port belongs to a different/unknown run; no cleanup performed')
    rows,_=profile_processes(config)
    baseline_path=run/'isolation-baseline.json'
    baseline=json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    changed=protected_changes(baseline) if baseline else []
    unsafe=bool(changed or (run/'isolation-alerts.jsonl').exists())
    calls=list((run/'mcp').glob('*/call-*.request.json'))
    # Include in-flight requests before their final metadata exists.
    posts=[p for p in (run/'http').glob('*.request.bin') if p.stat().st_size>0]
    if calls or posts or (status and status.get('inferenceRequests',0)):
        raise ValueError('Run has model/tool activity; automatic startup recovery will not discard an experiment')
    closed_path=run/'startup-recovery.json'
    if not status and not rows and closed_path.exists() and json.loads(closed_path.read_text()).get('action')=='cleaned':
        # A NEW explicit invocation may start afresh. The prior invocation still
        # stops immediately on an alert; do not rewrite its baseline or report.
        return {'action':'already-closed','run':str(run),'retryAllowed':True,'previousProtectedFileChanges':changed,'message':'Previous failed startup is closed and archived. This explicit start will create a fresh run; no daily files are restored.'}
    mains=[r for r in rows if r['name'].lower()=='chatgpt.exe' and r['role']=='main-or-cli']
    if mains and (run/'desktop-launch.json').exists() and not failed_attempt and not unsafe:
        return {'action':'reuse','run':str(run),'message':'Experiment Desktop is already running. Continue with -Verify; no duplicate launch.'}
    # Validate every identity BEFORE stopping anything.
    original_ids={r['pid'] for r in baseline['processes']} if baseline else set()
    for row in rows:
        env=row['environment']
        if row['pid'] in original_ids or env.get('GAV_DESKTOP_PROFILE_ID')!=run.name or normalize(env.get('CODEX_ELECTRON_USER_DATA_PATH','__missing__'))!=normalize(run/'electron-user-data'):
            raise ValueError('Unverified/daily Desktop process; refusing automatic cleanup')
    ready=json.loads((run/'ready.json').read_text()) if (run/'ready.json').exists() else {}
    support=json.loads((run/'support-process.json').read_text()) if (run/'support-process.json').exists() else {}
    renderer=json.loads((run/'renderer-process.json').read_text()) if (run/'renderer-process.json').exists() else {}
    support_pid=(status or {}).get('pid') or support.get('pid') or ready.get('pid')
    support_row=owned_python(support_pid,config,'scripts/desktop_environment.py',str(run/'config.json'),ready.get('started')) if support_pid else None
    renderer_pid=renderer.get('pid') or ready.get('rendererPid')
    renderer_row=owned_python(renderer_pid,config,'src/app.py','--port '+str(urlsplit(config['rendererService']).port),renderer.get('started',ready.get('started'))) if renderer_pid else None
    stopped=[]
    for row in rows:
        identity=process_info(row['pid'])
        if identity and identity['created']!=row['created']:raise ValueError('Profile PID changed; cleanup refused')
        if identity:terminate_verified(identity);stopped.append(row['pid'])
    if status and support_row:
        try:
            req=urllib.request.Request(config['recordingProxy']+'/desktop/stop',data=b'',method='POST')
            urllib.request.urlopen(req,timeout=3).close()
            for _ in range(6):
                if process_info(support_row['pid']) is None:break
                time.sleep(.25)
        except OSError:pass
    for row in [support_row,renderer_row]:
        if row:
            now=process_info(row['pid'])
            if now:
                if now['created']!=row['created']:raise ValueError('PID reused during cleanup; refusing termination')
                terminate_verified(now)
            stopped.append(row['pid'])
    if service_status(config) is not None:raise ValueError('Recording service still responds; no retry')
    report={'time':time.time(),'action':'cleaned','run':str(run),'reason':reason,'stoppedOwnedPids':stopped,'retryAllowed':not unsafe,'protectedChangedFiles':changed,'artifactsPreserved':True,'dailyConfigWritten':False}
    write_json(run/'startup-recovery.json',report)
    with (run/'startup-recovery-events.jsonl').open('a',encoding='utf-8') as output:output.write(json.dumps(report)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',default=str(ACTIVE));parser.add_argument('--failed-attempt',action='store_true');parser.add_argument('--reason',default='next-start recovery');args=parser.parse_args()
    path=Path(args.config)
    try:
        if not path.exists():result={'action':'none','retryAllowed':True}
        else:result=recover(json.loads(path.read_text(encoding='utf-8-sig')),args.failed_attempt,args.reason)
    except Exception as error:
        print(json.dumps({'action':'refused','retryAllowed':False,'error':str(error)},indent=2));raise SystemExit(3)
    print(json.dumps(result,indent=2))
    if result.get('retryAllowed') is False:raise SystemExit(2)

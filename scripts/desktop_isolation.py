"""Human-invoked Desktop launcher and passive evidence checks. Never an agent loop.

Workers may run static-audit/verify/protocol, NEVER launch/probe-second.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
import tomllib
import traceback
from pathlib import Path
from desktop_environment import ROOT, ACTIVE, digest, write_json
from windows_profile_probe import processes, inspect, normalize, within


def desktop_executable():
    raw=subprocess.check_output(['powershell.exe','-NoProfile','-Command',"[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); (Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1 -ExpandProperty InstallLocation)"],text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,timeout=20).strip()
    exe=Path(raw)/'app/ChatGPT.exe'
    if not raw or not exe.is_file():raise ValueError('Installed Desktop executable unavailable. Use a correctly registered installation; do not patch the package.')
    return exe


def static_audit(exe):
    asar=exe.parent/'resources/app.asar'
    excerpts={};source={}
    with asar.open('rb') as handle:
        fields=struct.unpack('<IIII',handle.read(16));header=json.loads(handle.read(fields[3]));offset=8+fields[1]
        for name,item in header['files']['.vite']['files']['build']['files'].items():
            if not name.endswith('.js') or 'offset' not in item:continue
            if not name.startswith(('bootstrap-','startup-requirements-','application-network-startup-')):continue
            handle.seek(offset+int(item['offset']));text=handle.read(item['size']).decode('utf-8')
            source[name]=text
            for key in ['CODEX_ELECTRON_USER_DATA_PATH','requestSingleInstanceLock({startupTask','setPath(`userData`','process.env.CODEX_HOME=']:
                index=text.find(key)
                if index>=0:excerpts[name+':'+key]=text[max(0,index-100):index+len(key)+220]
    joined='\n'.join(source.values())
    flags={'explicitGuiDirectory':'CODEX_ELECTRON_USER_DATA_PATH?.trim()' in joined,
           'setsGuiDirectory':'setPath(`userData`' in joined,
           'singleInstanceLock':'requestSingleInstanceLock({startupTask' in joined,
           'preservesExplicitHome':'process.env.CODEX_HOME=' in joined,
           'readsExplicitHome':'process.env.CODEX_HOME??' in joined}
    return {'desktopExecutable':str(exe),'asarSha256':digest(asar),'staticChecks':flags,'recognizedImplementation':all(flags.values()),'excerpts':excerpts,'runtimeIsolation':'not established by static inspection'}


def launch_environment(config, inherited=None):
    env=dict(os.environ if inherited is None else inherited)
    for key in list(env):
        if key.startswith(('CODEX_','SKY_CUA_','NODE_REPL_','OPENAI_','ANTHROPIC_')) or key in ['GOOGLE_API_KEY','GEMINI_API_KEY','OLLAMA_API_KEY']:
            env.pop(key,None)
    run=Path(config['resultDirectory']);home=Path(config['desktopHome']);gui=run/'electron-user-data'
    default=Path.home()/'.codex'
    if within(home,default) or within(gui,default) or not within(home,run) or not within(gui,run) or within(home,gui) or within(gui,home):
        raise ValueError('Profile directories must be disjoint children of this run, outside daily .codex')
    env.update(CODEX_HOME=str(home),CODEX_SQLITE_HOME=str(home),CODEX_ELECTRON_USER_DATA_PATH=str(gui),GAV_DESKTOP_PROFILE_ID=run.name)
    env['NO_PROXY']=env.get('NO_PROXY','')+',127.0.0.1,localhost'
    env['no_proxy']=env['NO_PROXY']
    return env


def protected_snapshot(rows):
    roots={Path(os.environ['APPDATA'])/'Codex'}
    for row in rows:
        if row.get('userDataDir'):roots.add(Path(row['userDataDir']))
    files={Path.home()/'.codex/config.toml'}
    for root in roots:
        for name in ['Preferences','Local State','Default/Preferences']:files.add(root/name)
    return {'guiRoots':sorted(str(p) for p in roots),'files':{str(p):digest(p) if p.is_file() else None for p in files},'processes':rows}


def protected_changes(snapshot):
    return [name for name,old in snapshot['files'].items() if (digest(Path(name)) if Path(name).is_file() else None)!=old]


def profile_processes(config, handles=False, protected_gui=()):
    home=config['desktopHome'];run=Path(config['resultDirectory']);gui=run/'electron-user-data';matches=[];unreadable=[]
    for row in processes():
        try:observed=inspect(row['pid'])
        except OSError as error:
            unreadable.append({'pid':row['pid'],'error':str(error)});continue
        env=observed['environment']
        if normalize(env.get('CODEX_HOME','__missing__'))!=normalize(home):continue
        entry={**row,'environment':env}
        if handles:
            try:
                opened=inspect(row['pid'],handles=True)['openDiskPaths']
                entry['homeHandles']=[p for p in opened if within(p,home)]
                entry['guiHandles']=[p for p in opened if within(p,gui)]
                entry['dailyHomeHandles']=[p for p in opened if within(p,Path.home()/'.codex')]
                entry['dailyGuiHandles']=[p for p in opened if any(within(p,root) for root in protected_gui)]
            except OSError as error:entry['handleProbeError']=str(error)
        matches.append(entry)
    return matches,unreadable


def verify(config, phase='running'):
    run=Path(config['resultDirectory']);baseline=json.loads((run/'isolation-baseline.json').read_text())
    rows,unreadable=profile_processes(config,handles=phase=='running',protected_gui=baseline['guiRoots'])
    home=Path(config['desktopHome']);gui=run/'electron-user-data';changed=protected_changes(baseline)
    mains=[r for r in rows if r['name'].lower()=='chatgpt.exe' and r['role']=='main-or-cli']
    servers=[r for r in rows if r['role']=='app-server']
    expected=lambda r:normalize(r['environment'].get('CODEX_ELECTRON_USER_DATA_PATH','__missing__'))==normalize(gui) and normalize(r['environment'].get('CODEX_SQLITE_HOME','__missing__'))==normalize(home)
    gui_handles=[p for r in rows for p in r.get('guiHandles',[])]
    home_handles=[p for r in rows for p in r.get('homeHandles',[])]
    daily_handles=[p for r in rows for p in r.get('dailyHomeHandles',[])]
    daily_gui_handles=[p for r in rows for p in r.get('dailyGuiHandles',[])]
    current=tomllib.loads((home/'config.toml').read_text(encoding='utf-8-sig'))
    checks={'dailyProtectedFilesUnchanged':not changed,'noLatchedIsolationAlert':not (run/'isolation-alerts.jsonl').exists(),'noObservedDailyHomeHandles':not daily_handles,'noObservedDailyGuiHandles':not daily_gui_handles}
    if phase=='running':
        checks.update(oneIndependentGuiMain=len(mains)==1 and mains[0]['pid'] not in [r['pid'] for r in baseline['processes']],
                      explicitEnvironmentObserved=bool(mains) and all(expected(r) for r in mains+servers),
                      actualGuiFilesOpen=bool(gui_handles),actualHomeFilesOpen=bool(home_handles),
                      nativeAppServerObserved=bool(servers),privateProviderConfigured=current.get('model_provider')=='volume_local',
                      privateCatalogDisablesLite=json.loads(Path(current['model_catalog_json']).read_text())['models'][0].get('use_responses_lite') is False)
    else:checks['profileProcessesExited']=not rows
    report={'time':time.time(),'phase':phase,'checks':checks,'passed':all(checks.values()),'protectedChangedFiles':changed,'profileProcesses':rows,'unreadableProcesses':unreadable,
            'limitations':'Observed paths and selected files only; not an OS security boundary or proof of all filesystem/network accesses. If daily app is active it can legitimately change its own files. No files are restored automatically.',
            'modelExperimentAcceptance':'pending actual request/MCP/image checks'}
    write_json(run/('isolation-'+phase+'.json'),report)
    return report


def launch(config, exe):
    """Human-only. Explicit child, no Explorer/shell activation and no prompt submission."""
    run=Path(config['resultDirectory']);baseline_path=run/'isolation-baseline.json'
    if baseline_path.exists():raise ValueError('This run was already launched; use verify/exit-check. Never reset its baseline.')
    audit=static_audit(exe);write_json(run/'desktop-static-audit.json',audit)
    if not audit['recognizedImplementation']:raise ValueError('Installed implementation changed; review static audit before launching')
    env=launch_environment(config);gui=Path(env['CODEX_ELECTRON_USER_DATA_PATH']);gui.mkdir()
    rows=processes();baseline=protected_snapshot(rows)
    if any(normalize(gui)==normalize(root) for root in baseline['guiRoots']):raise ValueError('GUI directory overlaps daily instance')
    write_json(baseline_path,baseline)
    # Main GUI intentionally visible: this is the human's interactive experiment.
    with (run/'desktop-stdout.log').open('wb') as out,(run/'desktop-stderr.log').open('wb') as err:
        child=subprocess.Popen([str(exe),'--user-data-dir='+str(gui)],env=env,cwd=ROOT,stdout=out,stderr=err)
    write_json(run/'desktop-launch.json',{'pid':child.pid,'time':time.time(),'executable':str(exe),'command':['ChatGPT.exe','--user-data-dir='+str(gui)],'environment':{k:env[k] for k in ['CODEX_HOME','CODEX_SQLITE_HOME','CODEX_ELECTRON_USER_DATA_PATH','GAV_DESKTOP_PROFILE_ID']}})
    time.sleep(3)
    if protected_changes(baseline):raise ValueError('Daily protected files changed; stop the isolated app manually and inspect. No automatic restore.')
    if child.poll() is not None:
        rows,_=profile_processes(config)
        if not any(r['name'].lower()=='chatgpt.exe' and r['role']=='main-or-cli' for r in rows):
            raise ValueError('Desktop child exited without an independent profile main; possible activation/instance failure. Inspect logs; do not submit a task in the daily app.')
    return {'startedPid':child.pid,'verification':'Run -Verify after the new GUI finishes startup; do not submit model prompts until isolation checks pass.'}


def probe_second(config, exe):
    """Human-only explicit single-instance test, not an Agent invocation."""
    before=verify(config)
    if not before['passed']:raise ValueError('Initial isolation checks must pass before second-instance test')
    run=Path(config['resultDirectory']);env=launch_environment(config);main=[r['pid'] for r in before['profileProcesses'] if r['name'].lower()=='chatgpt.exe' and r['role']=='main-or-cli']
    with (run/'second-instance-stdout.log').open('wb') as out,(run/'second-instance-stderr.log').open('wb') as err:
        child=subprocess.Popen([str(exe),'--user-data-dir='+env['CODEX_ELECTRON_USER_DATA_PATH']],env=env,cwd=ROOT,stdout=out,stderr=err)
        try:exit_code=child.wait(timeout=10)
        except subprocess.TimeoutExpired:exit_code=None # Do not kill an unverified GUI/process by name.
    after=verify(config);mains=[r['pid'] for r in after['profileProcesses'] if r['name'].lower()=='chatgpt.exe' and r['role']=='main-or-cli']
    result={'time':time.time(),'secondPid':child.pid,'secondExitCode':exit_code,'mainPidsBefore':main,'mainPidsAfter':mains,'passed':exit_code==0 and main==mains and after['passed'],'dailyProtectedFilesUnchanged':after['checks']['dailyProtectedFilesUnchanged']}
    write_json(run/'single-instance.json',result);return result


def protocol(config):
    run=Path(config['resultDirectory']);requests=[]
    for path in sorted((run/'http').glob('*.metadata.json')):
        meta=json.loads(path.read_text())
        if meta['method']!='POST' or meta['path']!='/v1/responses':continue
        body_path=path.with_name(path.name.replace('.metadata.json','.request.bin'));body=json.loads(body_path.read_bytes())
        types=[v.get('type','message') for v in body.get('input',[]) if isinstance(v,dict)]
        namespaces=[t.get('name') for t in body.get('tools',[]) if t.get('type')=='namespace']
        leaked_functions=[t.get('name') for t in body.get('tools',[]) if str(t.get('name','')).startswith('mcp__') and not str(t.get('name','')).startswith('mcp__volume_renderer')]
        requests.append({'file':str(body_path),'model':body.get('model'),'inputTypes':types,'hasAdditionalTools':'additional_tools' in types,'topLevelTools':len(body.get('tools') or []),'toolNamespaces':namespaces,'unexpectedNamespaces':[n for n in namespaces if n!='mcp__volume_renderer'],'unexpectedMcpFunctions':leaked_functions,'status':meta.get('status')})
    tool_calls=list((run/'mcp').glob('*/call-*.request.json'))
    result={'time':time.time(),'requests':requests,'mcpToolCallCount':len(tool_calls),'passed':bool(requests) and all(r['model']==config['model'] and not r['hasAdditionalTools'] and not r['unexpectedNamespaces'] and not r['unexpectedMcpFunctions'] and r['status']==200 for r in requests) and any(r['topLevelTools']>0 for r in requests) and bool(tool_calls),
            'imageAcceptance':'Run -Collect; require actual image transport matches and independent Task B trace/image review. Protocol pass alone is not a visual pass.'}
    write_json(run/'protocol-acceptance.json',result);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['static-audit','launch','verify','exit-check','probe-second','protocol']);parser.add_argument('--config',default=str(ACTIVE));parser.add_argument('--desktop-executable');args=parser.parse_args()
    if args.action=='static-audit':
        report=static_audit(Path(args.desktop_executable) if args.desktop_executable else desktop_executable());print(json.dumps(report,indent=2));return
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    if args.action in ['launch','probe-second']:
        try:
            exe=Path(args.desktop_executable) if args.desktop_executable else desktop_executable()
            report=launch(config,exe) if args.action=='launch' else probe_second(config,exe)
        except Exception as error:
            write_json(Path(config['resultDirectory'])/'desktop-launch-failure.json',{'time':time.time(),'action':args.action,'error':str(error),'traceback':traceback.format_exc()})
            print(str(error),file=sys.stderr);raise SystemExit(1)
    elif args.action=='protocol':report=protocol(config)
    else:report=verify(config,'exit' if args.action=='exit-check' else 'running')
    # Full process/file evidence stays in the report, keep console output small.
    print(json.dumps({k:v for k,v in report.items() if k not in ['profileProcesses','requests']},indent=2))
    if report.get('passed') is False:sys.exit(2)


if __name__=='__main__':main()

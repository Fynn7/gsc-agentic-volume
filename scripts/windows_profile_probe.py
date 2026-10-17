"""Read-only Windows process evidence. No app commands, UI, injection or model calls."""
import ctypes
from ctypes import wintypes as w
import json
import os
import re
import subprocess

PROFILE_KEYS=('CODEX_HOME','CODEX_ELECTRON_USER_DATA_PATH','CODEX_SQLITE_HOME','GAV_DESKTOP_PROFILE_ID')


def normalize(path):
    return os.path.normcase(os.path.abspath(str(path).removeprefix('\\\\?\\')))


def within(path, directory):
    try:return os.path.commonpath([normalize(path),normalize(directory)])==normalize(directory)
    except ValueError:return False


def processes():
    code="Get-CimInstance Win32_Process -Filter \"Name='ChatGPT.exe' OR Name='Codex.exe'\" | ForEach-Object { [PSCustomObject]@{pid=$_.ProcessId;parent=$_.ParentProcessId;name=$_.Name;executable=$_.ExecutablePath;command=$_.CommandLine;created=$_.CreationDate.ToUniversalTime().ToString('o')} } | ConvertTo-Json -Compress"
    code='[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); '+code
    raw=subprocess.check_output(['powershell.exe','-NoProfile','-Command',code],text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,timeout=20).strip()
    data=json.loads(raw) if raw else []
    if isinstance(data,dict):data=[data]
    result=[]
    for item in data:
        command=item.pop('command',None) or ''
        match=re.search(r'"--user-data-dir=([^"]+)"|--user-data-dir(?:=|\s+)(?:"([^"]+)"|([^\s"]+))',command)
        kind=re.search(r'--type=([^\s]+)',command)
        item['userDataDir']=next((v for v in match.groups() if v is not None),None) if match else None
        item['role']=kind.group(1) if kind else ('app-server' if 'app-server' in command else 'main-or-cli')
        result.append(item)
    return result


class WindowsProcess:
    def __init__(self,pid,duplicate=False):
        if os.name!='nt' or ctypes.sizeof(ctypes.c_void_p)!=8:raise OSError('64-bit native Windows Python is required')
        self.k=ctypes.WinDLL('kernel32',use_last_error=True);self.n=ctypes.WinDLL('ntdll')
        self.k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.k.OpenProcess.restype=w.HANDLE
        self.k.CloseHandle.argtypes=[w.HANDLE];self.k.CloseHandle.restype=w.BOOL
        self.k.ReadProcessMemory.argtypes=[w.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)];self.k.ReadProcessMemory.restype=w.BOOL
        self.n.NtQueryInformationProcess.argtypes=[w.HANDLE,w.ULONG,ctypes.c_void_p,w.ULONG,ctypes.POINTER(w.ULONG)];self.n.NtQueryInformationProcess.restype=w.LONG
        self.handle=self.k.OpenProcess(0x0400|0x0010|(0x0040 if duplicate else 0),False,pid)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
    def close(self):self.k.CloseHandle(self.handle)
    def read(self,address,size):
        value=ctypes.create_string_buffer(size);count=ctypes.c_size_t()
        if not self.k.ReadProcessMemory(self.handle,address,value,size,ctypes.byref(count)) or count.value!=size:raise ctypes.WinError(ctypes.get_last_error())
        return value.raw
    def environment(self):
        class Basic(ctypes.Structure):
            _fields_=[('reserved',ctypes.c_void_p),('peb',ctypes.c_void_p),('reserved2',ctypes.c_void_p*2),('pid',ctypes.c_void_p),('reserved3',ctypes.c_void_p)]
        value=Basic();length=w.ULONG()
        status=self.n.NtQueryInformationProcess(self.handle,0,ctypes.byref(value),ctypes.sizeof(value),ctypes.byref(length))
        if status<0:raise OSError('Process basic query failed: '+str(status))
        parameters=int.from_bytes(self.read(value.peb+0x20,8),'little')
        address=int.from_bytes(self.read(parameters+0x80,8),'little')
        # Bound reads to mapped memory, never dump unrelated environment values.
        class Region(ctypes.Structure):
            _fields_=[('base',ctypes.c_void_p),('allocation',ctypes.c_void_p),('allocationProtect',w.DWORD),('partition',w.WORD),('regionSize',ctypes.c_size_t),('state',w.DWORD),('protect',w.DWORD),('type',w.DWORD)]
        self.k.VirtualQueryEx.argtypes=[w.HANDLE,ctypes.c_void_p,ctypes.POINTER(Region),ctypes.c_size_t];self.k.VirtualQueryEx.restype=ctypes.c_size_t
        region=Region()
        if not self.k.VirtualQueryEx(self.handle,address,ctypes.byref(region),ctypes.sizeof(region)):raise ctypes.WinError(ctypes.get_last_error())
        size=min(region.base+region.regionSize-address,2*1024*1024)
        block=bytearray()
        for offset in range(0,size,512):
            block.extend(self.read(address+offset,min(512,size-offset)))
            text=block.decode('utf-16-le',errors='replace')
            if '\x00\x00' in text:break
        else:raise OSError('No bounded environment terminator found')
        raw=text.split('\x00\x00',1)[0]
        pairs=[item.split('=',1) for item in raw.split('\x00') if '=' in item and not item.startswith('=')]
        return {key:value for key,value in pairs if key in PROFILE_KEYS}
    def disk_paths(self):
        # ProcessHandleInformation avoids scanning all system handles or named pipes.
        class Entry(ctypes.Structure):
            _fields_=[('handle',w.HANDLE),('handleCount',ctypes.c_size_t),('pointerCount',ctypes.c_size_t),('access',w.ULONG),('type',w.ULONG),('attributes',w.ULONG),('reserved',w.ULONG)]
        length=w.ULONG();size=65536
        while size<=16*1024*1024:
            data=ctypes.create_string_buffer(size)
            status=self.n.NtQueryInformationProcess(self.handle,51,data,size,ctypes.byref(length))
            if status>=0:break
            size=max(size*2,length.value)
        else:raise OSError('Handle snapshot unavailable or too large')
        count=int.from_bytes(data.raw[:8],'little');count=min(count,(size-16)//ctypes.sizeof(Entry))
        self.k.GetCurrentProcess.restype=w.HANDLE
        self.k.DuplicateHandle.argtypes=[w.HANDLE,w.HANDLE,w.HANDLE,ctypes.POINTER(w.HANDLE),w.DWORD,w.BOOL,w.DWORD];self.k.DuplicateHandle.restype=w.BOOL
        self.k.GetFileType.argtypes=[w.HANDLE];self.k.GetFileType.restype=w.DWORD
        self.k.GetFinalPathNameByHandleW.argtypes=[w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD];self.k.GetFinalPathNameByHandleW.restype=w.DWORD
        paths=set()
        for index in range(count):
            entry=Entry.from_buffer_copy(data.raw,16+index*ctypes.sizeof(Entry));duplicate=w.HANDLE()
            if not self.k.DuplicateHandle(self.handle,entry.handle,self.k.GetCurrentProcess(),ctypes.byref(duplicate),0,False,2):continue
            try:
                if self.k.GetFileType(duplicate)!=1:continue
                name=ctypes.create_unicode_buffer(32768)
                length=self.k.GetFinalPathNameByHandleW(duplicate,name,len(name),0)
                if 0<length<len(name):paths.add(name.value.removeprefix('\\\\?\\'))
            finally:self.k.CloseHandle(duplicate)
        return sorted(paths)


def inspect(pid,handles=False):
    process=WindowsProcess(pid,duplicate=handles)
    try:
        result={'environment':process.environment()}
        if handles:result['openDiskPaths']=process.disk_paths()
        return result
    finally:process.close()

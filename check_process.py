"""Bounded verification pipes and owned process-tree lifetime; no OS sandbox."""
import os
import signal
import subprocess
import threading


class Tail:
    def __init__(self,limit=32768):self.limit=limit;self.data=b'';self.truncated=False
    def drain(self,stream):
        try:
            while block:=stream.read(8192):
                self.truncated=self.truncated or len(self.data)+len(block)>self.limit
                self.data=(self.data+block)[-self.limit:]
        finally:stream.close()


class ProcessTree:
    def __init__(self):
        self.handle=None;self.pid=None
        if os.name!='nt':return
        import ctypes
        from ctypes import wintypes as w
        self.ctypes=ctypes;self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),
                      ('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('process_limit',w.DWORD),
                      ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class Limits(ctypes.Structure):
            _fields_=[('basic',Basic),('io',ctypes.c_ulonglong*6),('process_memory',ctypes.c_size_t),
                      ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        k=self.kernel
        k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
        k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];k.SetInformationJobObject.restype=w.BOOL
        k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
        k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.AssignProcessToJobObject.restype=w.BOOL
        k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
        self.handle=k.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Limits();limits.basic.flags=0x2000
        if not k.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error=ctypes.WinError(ctypes.get_last_error());self.close();raise error

    def assign(self,process):
        self.pid=process.pid
        if os.name!='nt':return
        handle=self.kernel.OpenProcess(0x0101,False,process.pid)
        if not handle:raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle,handle):raise self.ctypes.WinError(self.ctypes.get_last_error())
        finally:self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle is not None:
            handle=self.handle;self.handle=None
            if not self.kernel.CloseHandle(handle):raise self.ctypes.WinError(self.ctypes.get_last_error())
        elif os.name!='nt' and self.pid is not None:
            try:os.killpg(self.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            finally:self.pid=None


def execute(command,folder,timeout):
    tree=ProcessTree();process=None;threads=[];out=Tail();err=Tail();timed_out=False
    try:
        process=subprocess.Popen(command,cwd=folder,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 bufsize=0,start_new_session=os.name!='nt',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        # RUNNER waits on stdin before importing workspace code.
        tree.assign(process)
        for buffer,stream in ((out,process.stdout),(err,process.stderr)):
            thread=threading.Thread(target=buffer.drain,args=(stream,),daemon=True);thread.start();threads.append(thread)
        process.stdin.write(b'1');process.stdin.close()
        try:process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:timed_out=True
    finally:
        try:tree.close()
        finally:
            if process is not None:
                if process.stdin and not process.stdin.closed:process.stdin.close()
                if process.poll() is None:process.kill()
                process.wait(timeout=5)
                if not threads:process.stdout.close();process.stderr.close()
            for thread in threads:thread.join(timeout=3)
    if any(thread.is_alive() for thread in threads):raise OSError('검증 출력 파이프를 정리하지 못했습니다.')
    return {'returncode':process.returncode,'timed_out':timed_out,
            'stdout':out.data.decode('utf-8',errors='replace'),'stderr':err.data.decode('utf-8',errors='replace'),
            'native_output_truncated':out.truncated or err.truncated}

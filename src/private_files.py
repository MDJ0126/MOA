"""Create a new user/SYSTEM-only directory before writing install credentials."""
import ctypes
import os
from pathlib import Path

def windows_api():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);adv=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel.GetCurrentProcess.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.LocalFree.argtypes=[ctypes.c_void_p]
    adv.OpenProcessToken.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_void_p)]
    adv.GetTokenInformation.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong)]
    adv.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_wchar_p)]
    return kernel,adv

def current_sid():
    kernel,adv=windows_api();token=ctypes.c_void_p();needed=ctypes.c_ulong()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):raise ctypes.WinError(ctypes.get_last_error())
    try:
        adv.GetTokenInformation(token,1,None,0,ctypes.byref(needed));buffer=ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token,1,buffer,len(buffer),ctypes.byref(needed)):raise ctypes.WinError(ctypes.get_last_error())
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0];text=ctypes.c_wchar_p()
        if not adv.ConvertSidToStringSidW(sid,ctypes.byref(text)):raise ctypes.WinError(ctypes.get_last_error())
        try:return text.value
        finally:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))
    finally:kernel.CloseHandle(token)

def security_sddl(path):
    kernel,adv=windows_api();needed=ctypes.c_ulong();text=ctypes.c_wchar_p()
    adv.GetFileSecurityW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.POINTER(ctypes.c_wchar_p),ctypes.POINTER(ctypes.c_ulong)]
    adv.GetFileSecurityW(str(path),4,None,0,ctypes.byref(needed));buffer=ctypes.create_string_buffer(needed.value)
    if not adv.GetFileSecurityW(str(path),4,buffer,len(buffer),ctypes.byref(needed)):raise ctypes.WinError(ctypes.get_last_error())
    if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(buffer,1,4,ctypes.byref(text),None):raise ctypes.WinError(ctypes.get_last_error())
    try:return text.value
    finally:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))

def private_directory(path):
    path=Path(path)
    if os.name!='nt':
        path.mkdir(mode=0o700)
        if path.stat().st_mode&0o077:raise ValueError('설치 보호 폴더 권한을 확인하지 못했습니다.')
        return path
    kernel,adv=windows_api();sid=current_sid();descriptor=ctypes.c_void_p()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_ulong)]
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;'+sid+')',1,ctypes.byref(descriptor),None):raise ctypes.WinError(ctypes.get_last_error())
    class Attributes(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('descriptor',ctypes.c_void_p),('inherit',ctypes.c_int)]
    attributes=Attributes(ctypes.sizeof(Attributes),descriptor,0)
    kernel.CreateDirectoryW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(Attributes)]
    try:
        if not kernel.CreateDirectoryW(str(path),ctypes.byref(attributes)):raise ctypes.WinError(ctypes.get_last_error())
    finally:kernel.LocalFree(descriptor)
    actual=security_sddl(path)
    if 'D:P' not in actual or actual.count('(A;')!=2 or ';;;SY)' not in actual or ';;;'+sid+')' not in actual:raise ValueError('설치 보호 폴더 ACL을 확인하지 못했습니다.')
    return path

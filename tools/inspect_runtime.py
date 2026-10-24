"""Record local process memory and model sleep/auth state, without GUI control."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import ctypes
import datetime
import json
from pathlib import Path
import urllib.error
import urllib.request
from local_model import headers

root=Path(__file__).resolve().parent.parent
class Memory(ctypes.Structure):
    _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in ('total','available','pageTotal','pageAvailable','virtualTotal','virtualAvailable','extended')]
m=Memory();m.length=ctypes.sizeof(m)
ok=ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
result={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'query_ok':bool(ok),'ram_gib':round(m.total/2**30,2),'available_ram_gib':round(m.available/2**30,2)}
for name,path,auth in [('model_health','/health',True),('model_properties','/props',True),('unauthenticated_model','/v1/models',False)]:
    try:
        request=urllib.request.Request('http://127.0.0.1:8766'+path,headers=headers('http://127.0.0.1:8766') if auth else {})
        with urllib.request.urlopen(request,timeout=5) as response:
            value=json.load(response)
            result[name]={'status':response.status,'sleeping':value.get('is_sleeping'),'model':value.get('model_path'),'health':value.get('status')}
    except urllib.error.HTTPError as e:result[name]={'status':e.code}
    except OSError as e:result[name]={'error':str(e)}
(root/'data'/'runtime-inspection.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result),flush=True)

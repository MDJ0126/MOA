"""Validate authenticated inference, idle unloading, and live dashboard readiness."""
import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
import ctypes
import datetime
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from local_model import headers

root=Path(__file__).resolve().parent.parent
base='http://127.0.0.1:8766'
def get(path):
    with urllib.request.urlopen(urllib.request.Request(base+path,headers=headers(base)),timeout=5) as r:return json.load(r)
def memory():
    class M(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','p1','p2','v1','v2','ex')]
    m=M();m.length=ctypes.sizeof(m);ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m));return round(m.available/2**30,2)

for _ in range(120):
    try:get('/health');break
    except OSError:time.sleep(.5)
else:raise RuntimeError('Model server did not become ready')
try:
    urllib.request.urlopen(base+'/v1/models',timeout=5)
    raise AssertionError('Unauthenticated model request was allowed')
except urllib.error.HTTPError as e:assert e.code==401,e
payload={'model':'qwen-local','messages':[{'role':'user','content':'Reply with the single word READY.'}],'max_tokens':10,'temperature':0}
request=urllib.request.Request(base+'/v1/chat/completions',json.dumps(payload).encode(),headers(base))
with urllib.request.urlopen(request,timeout=60) as response:reply=json.load(response)['choices'][0]['message']['content']
assert 'READY' in reply,reply
loaded=memory();started=time.time()
config=json.loads((root/'config.json').read_text(encoding='utf-8'))
idle_seconds=int(config.get('sleep_idle_seconds',300))
if idle_seconds<=0:raise RuntimeError('Idle unloading verification requires sleep_idle_seconds > 0')
for _ in range(idle_seconds+60):
    props=get('/props')
    if props.get('is_sleeping'):break
    time.sleep(1)
else:raise RuntimeError('Idle model did not unload')
with urllib.request.urlopen('http://127.0.0.1:8765/api/state',timeout=5) as response:state=json.load(response)
result={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'authenticated_inference':reply,
        'unauthenticated_status':401,'is_sleeping':props['is_sleeping'],'idle_sleep_observed_after_seconds':round(time.time()-started,2),
        'available_ram_loaded_gib':loaded,'available_ram_sleeping_gib':memory(),'dashboard_model':state.get('model_description'),
        'supervisor':state['supervisor'],'scope':'short runtime verification, not a 24-hour soak test'}
(root/'data'/'runtime-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result),flush=True)

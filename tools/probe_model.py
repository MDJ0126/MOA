import sys as _sys
from pathlib import Path as _Path
_PROJECT = _Path(__file__).resolve().parent.parent
_sys.path[:0] = [str(_PROJECT/'src'), str(_PROJECT/'tools'), str(_PROJECT)]
from app import ROOT, LocalFiles, CONFIG
from local_model import Llama
from checks import run_checks
from verify_real import TITLE, ACCEPTANCE
import json

files=LocalFiles(ROOT/'data'/'model-probe'/'workspace')
model=Llama(CONFIG['model'],CONFIG['model_url'])
task={'title':TITLE,'acceptance':ACCEPTANCE}
events=[]
for target in ('health.py','test_health.py'):
    model.generate_file(task,target,files,lambda kind,content:events.append({'kind':kind,'content':content}))
result=run_checks(files.folder,ACCEPTANCE)
(files.folder.parent/'verification.json').write_text(json.dumps({'verification':result,'events':events},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False),flush=True)
assert result['passed'],result

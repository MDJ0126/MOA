"""Real Qwen end-to-end validation with independent, fixed acceptance tests."""
import json
from pathlib import Path
import time
import urllib.request
from app import ROOT, Store, Worker, CONFIG, LocalFiles

ACCEPTANCE='''import unittest
from health import clamp_health
class Acceptance(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(clamp_health(-5),0)
        self.assertEqual(clamp_health(150),100)
        self.assertEqual(clamp_health(42),42)
    def test_custom_maximum(self):
        self.assertEqual(clamp_health(90,50),50)
    def test_invalid_maximum(self):
        with self.assertRaises(ValueError):clamp_health(1,-1)
'''
TITLE='Create health.py with clamp_health(value, maximum=100) for a game. Return the value clamped to [0, maximum]. Raise ValueError if maximum < 0. Include unittest tests in test_health.py. Use standard library only. Create complete runnable files.'

def main():
    started=time.time()
    store=Store(ROOT/'data')
    def control(action):
        request=urllib.request.Request('http://127.0.0.1:8765/api/control',json.dumps({'action':action}).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
        with urllib.request.urlopen(request,timeout=3):pass
    def wait(tid):
        for _ in range(3600):
            t=next(t for t in store.snapshot()['tasks'] if t['id']==tid)
            if t['status'] in ('completed','failed'):
                print(json.dumps({'task':tid,'status':t['status'],'stage':t['stage'],'revision':t['revision'],'error':t['error']},ensure_ascii=False),flush=True)
                return t
            time.sleep(.1)
        raise RuntimeError('Actual inference task timed out')
    control('pause')
    tid=store.add('[실제 Qwen 검증] '+TITLE,'llama',acceptance=ACCEPTANCE,required_files=['health.py','test_health.py'])
    control('resume');t=wait(tid)
    if t['status']!='completed':raise RuntimeError(t)
    # Deliberately seed a known defect to verify reviewer -> developer repair.
    control('pause')
    repair=store.add('[실제 Qwen 수정 검증] '+TITLE,'llama',acceptance=ACCEPTANCE,required_files=['health.py','test_health.py'])
    files=LocalFiles(ROOT/'data'/'tasks'/repair/'workspace')
    files.execute({'tool':'write','path':'health.py','content':'def clamp_health(value, maximum=100):\n    return value\n'},True)
    with store.connect() as c:c.execute('UPDATE tasks SET stage=2 WHERE id=?',(repair,))
    store.log(repair,'system','seeded_failure','검증을 위해 return value 결함을 의도적으로 주입했습니다. 모델이 최초 생성한 결함이 아닙니다.')
    control('resume');r=wait(repair)
    assert r['status']=='completed',r
    assert r['revision']>=1,r
    result={'model':CONFIG['model_file'],'runtime':'llama.cpp b11669 CPU 4 threads',
            'tasks':[tid,repair],'fixed_acceptance_tests_per_task':3,'elapsed_seconds':round(time.time()-started,2),
            'real_inference':True,'repair_test':'known defect was deliberately seeded by verification script',
            'verified_required_files':['health.py','test_health.py'],
            'verification_results':[json.loads(t['verification']),json.loads(r['verification'])],
            'limitations':['No game engine or Slack connected','24-hour continuous soak test not yet performed']}
    (ROOT/'data'/'real-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()

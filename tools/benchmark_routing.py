"""Sequential isolated real-model A/B; restore production service in finally."""
import sys,json,time,subprocess,urllib.request,types,copy,datetime,ctypes
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from setup_config import PRESETS
from organization import instruction
from leader_inbox import LeaderInbox,validate
from app_lifecycle import request_stop
OUT=ROOT/'reports/performance-20261025'
BASE='http://127.0.0.1:18766'
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)

def get(url):
    with urllib.request.urlopen(url,timeout=3) as r:return json.load(r)

def memory():
    class M(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(k,ctypes.c_ulonglong) for k in ('total','available','p1','p2','v1','v2','ex')]
    m=M();m.length=ctypes.sizeof(m);ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m));return round(m.available/2**30,3)

class Captured(Exception):pass
def body(module,payload):
    def capture(req,timeout):
        module.captured=json.loads(req.data);raise Captured()
    inbox=object.__new__(module.LeaderInbox);inbox.model='qwen-local';inbox.url=BASE
    with patch.object(module,'request_json',side_effect=capture):
        try:inbox.decide(payload)
        except Captured:pass
    result=module.captured;result.update(seed=42,temperature=0)
    return result

def infer(payload):
    started=time.perf_counter()
    with urllib.request.urlopen(urllib.request.Request(BASE+'/v1/chat/completions',json.dumps(payload,ensure_ascii=False).encode(),{'Content-Type':'application/json'}),timeout=300) as r:response=json.load(r)
    return {'seconds':round(time.perf_counter()-started,3),'usage':response.get('usage'),
            'timings':response.get('timings'),'content':response['choices'][0]['message']['content'],
            'finish_reason':response['choices'][0].get('finish_reason')}

def run():
    import leader_inbox as after
    before=types.ModuleType('leader_before');exec(compile((OUT/'leader_inbox_before.py').read_text(encoding='utf-8'),'before','exec'),before.__dict__)
    agents=copy.deepcopy(PRESETS[2]['agents'])
    payload={'title':'ready.py 파일에 add(a, b) 함수를 구현해 주세요. 두 수의 합을 반환해야 합니다. 음수와 소수도 지원하고 독립 검증을 해 주세요.',
             'required_files':['ready.py'],'acceptance':'add(2,3)==5; add(-2,1)==-1; add(0.5,1.5)==2.0',
             'agents':agents,'workspace':{'global_instructions':'요청한 함수명과 파일명을 보존하고 실행하지 않은 검증을 통과했다고 보고하지 마세요.\n\n'+instruction(agents),'skills':[]}}
    (OUT/'request.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    phases={name:body(module,payload) for name,module in [('before',before),('after',after)]}
    for name,data in phases.items():(OUT/(name+'-request.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    summary={'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Real 7B CPU leader routing cold/repeated request, intervening greeting to exercise prompt cache, and real generated-file execution. One request, fixed seed, model, threads and context. Not a complete VM/Slack workflow benchmark.','phases':{},'memory_before_gib':memory()}
    if '--resume' in sys.argv and (OUT/'results.json').exists():
        previous=json.loads((OUT/'results.json').read_text(encoding='utf-8'));summary['phases']=previous.get('phases',{});summary['started']=previous.get('started',summary['started'])
    restore=False;server=None
    try:
        try:
            health=get('http://127.0.0.1:8765/api/health');restore=True
        except OSError:health=None
        if health:
            deadline=time.monotonic()+300
            while health['queue']['active'] or health['queue']['counts'].get('queued',0):
                print('Waiting for existing production task to finish: '+json.dumps(health['queue'],ensure_ascii=False),flush=True)
                if time.monotonic()>deadline:raise TimeoutError('Existing work did not finish; production was not stopped')
                time.sleep(10);health=get('http://127.0.0.1:8765/api/health')
            (ROOT/'data/supervisor.stop').touch();request_stop(ROOT,8765)
            for _ in range(60):
                try:get('http://127.0.0.1:8765/api/health')
                except OSError:break
                time.sleep(1)
            else:raise TimeoutError('Production did not stop')
            time.sleep(3)
        for name,cache,idle in [('before',64,30),('after',config['cache_ram_mib'],config['sleep_idle_seconds'])]:
            print('START '+name,flush=True)
            with (OUT/(name+'-server.log')).open('w',encoding='utf-8') as log:
                command=[str(ROOT/config['runtime_executable']),'-m',str(ROOT/config['model_file']),'--host','127.0.0.1','--port','18766','--ctx-size',str(config['context_size']),'--parallel','1','--threads',str(config['threads']),'--gpu-layers','0','--alias',config['model'],'--cache-ram',str(cache),'--sleep-idle-seconds',str(idle)]
                server=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,creationflags=FLAGS)
                for _ in range(120):
                    try:get(BASE+'/health');break
                    except OSError:
                        if server.poll() is not None:raise RuntimeError('Benchmark server exited')
                        time.sleep(.5)
                else:raise TimeoutError('Benchmark server not ready')
                results=summary['phases'].get(name,{}).get('results',[])
                completed={r['label'] for r in results}
                for label,data in [('cold',phases[name]),('intervening',{'model':config['model'],'messages':[{'role':'user','content':'Reply only READY.'}],'max_tokens':8,'temperature':0,'seed':42}),('repeat',phases[name])]:
                    if label in completed:continue
                    print(name+' '+label,flush=True);result=infer(data);result['label']=label
                    if label!='intervening':
                        raw=json.loads(result['content']);decision=validate(agents,raw,payload)
                        checks={'valid_schema':result['finish_reason']=='stop','work_kind_code':decision.get('work_kind')=='code',
                                'execution_assigned':any(a['role']=='developer' for a in decision['assignments']),
                                'independent_review':len({a['agent_id'] for a in decision['assignments'] if a['role'] in ('developer','reviewer')})==2,
                                'no_false_completion':raw['action']!='reply'}
                        result.update(decision=decision,checks=checks)
                    results.append(result);print(json.dumps({k:result[k] for k in ('label','seconds','usage','timings')},ensure_ascii=False),flush=True)
                    summary['phases'][name]={'cache_ram_mib':cache,'sleep_idle_seconds':idle,'results':results,'memory_available_gib':memory()}
                    (OUT/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                source_request={'model':config['model'],'messages':[{'role':'system','content':'Return only JSON with content containing a complete Python file. No Markdown.'},{'role':'user','content':payload['title']+'\n'+payload['acceptance']}],
                    'temperature':0,'seed':42,'max_tokens':200,'response_format':{'type':'json_schema','json_schema':{'name':'source_file','strict':True,'schema':{'type':'object','properties':{'content':{'type':'string'}},'required':['content'],'additionalProperties':False}}}}
                generated=infer(source_request);(OUT/(name+'-generation.json')).write_text(json.dumps(generated,ensure_ascii=False,indent=2),encoding='utf-8');source=json.loads(generated['content'])['content'];(OUT/(name+'-ready.py')).write_text(source,encoding='utf-8')
                checked=subprocess.run([sys.executable,'-I','-c',source+'\nassert add(2,3)==5\nassert add(-2,1)==-1\nassert add(0.5,1.5)==2.0\nassert add(0,0)==0\nprint("4 checks passed")'],capture_output=True,text=True,timeout=10,creationflags=FLAGS)
                generated.update(passed=checked.returncode==0,stdout=checked.stdout,stderr=checked.stderr)
                summary['phases'][name]['generated_file']=generated
                (OUT/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                server.terminate();server.wait(timeout=15);server=None
        summary['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    finally:
        if server and server.poll() is None:server.terminate();server.wait(timeout=15)
        if restore:
            try:get('http://127.0.0.1:8765/api/health')
            except OSError:
                with (OUT/'production-restart.stdout.log').open('a') as out,(OUT/'production-restart.stderr.log').open('a') as err:
                    proc=subprocess.Popen([sys.executable,str(ROOT/'src/supervisor.py')],cwd=ROOT,stdout=out,stderr=err,creationflags=FLAGS)
                    summary['restarted_supervisor_pid']=proc.pid
        (OUT/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DONE',flush=True)

if __name__=='__main__':run()

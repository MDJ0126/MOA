"""Persistent, evidence-only boot durations keyed by real QEMU boot session."""
import json,math,time

KEY='vm_boot_history'
def observe(store,session,power,readiness,now=None,configured=None):
 now=time.time() if now is None else now
 config={k:(session.get('config') or configured or {}).get(k) for k in ('guest_os','disk','memory_mb','cpus','accelerator')}
 started=session.get('started');valid=type(started) in (int,float) and math.isfinite(started) and 0<=started<=now
 boot_id=session.get('name');install=session.get('config',{}).get('installation_only',False)
 with store.connect() as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT value FROM settings WHERE key=?',(KEY,)).fetchone()
  try:history=json.loads(row[0]) if row else []
  except (ValueError,TypeError):return {'active':False,'history_error':'부팅 기록을 읽지 못했습니다. 원본을 보존했습니다.'}
  if not isinstance(history,list) or any(not isinstance(item,dict) for item in history):return {'active':False,'history_error':'부팅 기록 형식 확인 필요'}
  before=json.dumps(history,ensure_ascii=False);entry=next((r for r in history if r.get('session')==boot_id),None)
  if entry is None and valid and boot_id and power=='running' and not install:
   entry={'session':boot_id,'started_at':started,'config':config,'state':'booting'};history.append(entry)
  if entry is not None:
   if power=='running' and readiness.get('state')=='ready' and entry['state']!='ready':
    ready_at=readiness.get('checked_at',now)
    if type(ready_at) not in (int,float) or not entry['started_at']<=ready_at<=now:ready_at=now
    entry.update(state='ready',ready_at=ready_at,duration_seconds=round(ready_at-entry['started_at'],2))
   elif power=='stopped' and entry['state']=='booting':entry.update(state='interrupted',ended_at=now)
  history=history[-20:]
  if json.dumps(history,ensure_ascii=False)!=before:c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(history,ensure_ascii=False)))
 previous=[r for r in history if r.get('state')=='ready' and r.get('config')==config and type(r.get('duration_seconds')) in (int,float)]
 last=previous[-1] if previous else None;estimate=last['duration_seconds'] if last else None
 active=power=='running' and readiness.get('state')!='ready' and not install
 elapsed=max(0,now-started) if valid else None
 delayed=bool(active and elapsed is not None and (elapsed>estimate if estimate is not None else elapsed>=300))
 return {'active':active,'session':boot_id,'started_at':started if valid else None,'elapsed_seconds':round(elapsed,1) if elapsed is not None else None,'estimated_seconds':estimate,'last_success_seconds':estimate,'average_success_seconds':round(sum(r['duration_seconds'] for r in previous)/len(previous),2) if previous else None,'successful_boot_count':len(previous),'last_success_at':last.get('ready_at') if last else None,'remaining_seconds':max(0,round(estimate-elapsed,1)) if estimate is not None and elapsed is not None else None,'delayed':delayed,'no_history_delay_threshold_seconds':300,'history':history[-5:]}

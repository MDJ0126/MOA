"""Leader-owned, measurable personal goals and comparisons across comparable tasks."""
import json,time

def initialize(c):
 c.execute('CREATE TABLE IF NOT EXISTS team_growth(task_id TEXT,agent_id TEXT,leader_id TEXT,role TEXT,work_kind TEXT,goal TEXT,baseline TEXT,state TEXT DEFAULT \'assigned\',metrics TEXT DEFAULT \'{}\',points INTEGER DEFAULT 0,reason TEXT DEFAULT \'\',created REAL,evaluated REAL,PRIMARY KEY(task_id,agent_id))')

GOALS={
 'developer':'직전 동종 업무에서 지적된 재작업을 줄이고, 실제 실행 성공 근거를 남긴 뒤 검증 담당자에게 인계하세요.',
 'reviewer':'기존 결과를 독립적으로 검증하고 실패·누락을 실제 명령과 근거로 지적하세요. 정상 통과를 특별 성과로 과장하지 마세요.',
 'planner':'요청 범위·담당자·완료 기준을 구체화하고, 이전 동종 업무의 누락·불필요한 인계를 줄이세요.',
 'reporter':'검증된 결과와 실제 출처·실행 근거를 빠짐없이 인계하고, 이전 동종 업무의 근거 누락을 줄이세요.'}

def bind(store,task,plan):
 chief=plan.get('leader_review',{}).get('leader_id') or next((a['id'] for a in plan.get('agents',[]) if not a.get('manager_id')),next(iter(plan['assignment'].values())))
 with store.connect() as c:
  for aid in dict.fromkeys(plan['assignment'].values()):
   role=next((r for r in ('developer','reviewer','reporter','planner') if plan['assignment'].get(r)==aid),'planner');kind=plan.get('work_kind','general')
   prior=c.execute("SELECT task_id,metrics FROM team_growth WHERE agent_id=? AND role=? AND work_kind=? AND state='evaluated' AND task_id<>? ORDER BY created DESC LIMIT 1",(aid,role,kind,task['id'])).fetchone()
   baseline={'task_id':prior['task_id'],'metrics':json.loads(prior['metrics'])} if prior else {}
   if not prior:
    candidates=c.execute("SELECT t.* FROM tasks t WHERE t.id<>? AND t.status IN ('completed','failed') AND t.mode<>'demo' AND EXISTS(SELECT 1 FROM messages m WHERE m.task_id=t.id AND m.agent=? AND m.kind IN ('handoff','guest_result')) ORDER BY t.created DESC LIMIT 30",(task['id'],aid)).fetchall()
    for old in candidates:
     try:old_plan=json.loads(old['execution_plan'])
     except (ValueError,TypeError):continue
     if old_plan.get('work_kind','general')!=kind or old_plan.get('assignment',{}).get(role)!=aid:continue
     old_rows=[dict(r) for r in c.execute('SELECT seq,agent,kind,content FROM messages WHERE task_id=? ORDER BY seq',(old['id'],))]
     baseline={'task_id':old['id'],'metrics':collect_metrics(c,old['id'],aid,role,json.loads(old['verification'] or '{}'),old_rows),'source':'existing_task_evidence'};break

   if prior:
    baseline['leader_feedback']=[dict(r) for r in c.execute("SELECT reason,instructions FROM leader_reviews WHERE task_id=? AND decision='revise' ORDER BY round DESC LIMIT 2",(prior['task_id'],))]
    baseline['rejection_feedback']=[r[0][:400] for r in c.execute("SELECT content FROM messages WHERE task_id=? AND kind='review_rejection' ORDER BY seq DESC LIMIT 2",(prior['task_id'],))]
   assignment=next((a.get('instruction','') for a in plan.get('leader_workflow',{}).get('assignments',[]) if a.get('role')==role),'')
   c.execute('INSERT OR IGNORE INTO team_growth(task_id,agent_id,leader_id,role,work_kind,goal,baseline,created) VALUES(?,?,?,?,?,?,?,?)',(task['id'],aid,chief,role,kind,GOALS[role]+(' 리더 배정: '+assignment[:350] if assignment else ''),json.dumps(baseline,ensure_ascii=False),time.time()))

def instructions(store,tid,aid):
 with store.connect() as c:r=c.execute('SELECT goal,baseline FROM team_growth WHERE task_id=? AND agent_id=?',(tid,aid)).fetchone()
 return ('\n[리더 개인 개선 목표]\n'+r['goal']+'\n직전 동종 업무 기준: '+r['baseline']+'\n보통의 업무 완료는 0점입니다. 실제 전후 근거가 있는 개선만 가점합니다. 점수보다 사용자 업무와 정확성을 우선하세요.') if r else ''

def collect_metrics(c,tid,aid,role,verification,rows):
 own=[r for r in rows if r['agent']==aid];commands=[];handoffs=[]
 for r in own:
  try:value=json.loads(r['content'])
  except (ValueError,TypeError):continue
  if not isinstance(value,dict):continue
  if r['kind']=='guest_result' and type(value.get('exit_code')) is int:commands.append((r['seq'],value))
  if r['kind']=='handoff':
   handoffs.append((r['seq'],value))
   if not commands:
    for result in value.get('guest_results',[]):
     if isinstance(result,dict) and type(result.get('exit_code')) is int:commands.append((r['seq'],result))
 rework=0
 if role=='developer':rework=sum(r['kind']=='review_rejection' for r in rows)+c.execute("SELECT COUNT(*) FROM leader_reviews WHERE task_id=? AND decision='revise'",(tid,)).fetchone()[0]
 final_failure=bool(commands and commands[-1][1]['exit_code'] not in (0,85,127))
 evidence=bool(commands or any(v.get('guest_results') or v.get('research') or v.get('files') or v.get('required_files') for _,v in handoffs))
 verified=bool(commands and commands[-1][1]['exit_code']==0) if role=='developer' else bool(verification.get('passed') and verification.get('tests_run',0)>0) if role=='reviewer' else bool(handoffs and (evidence or role=='planner'))
 metrics={'evidence':int(evidence),'verified':int(verified),'no_rework':int(rework==0),'rework':rework,'final_failure':final_failure,'evidence_seqs':[seq for seq,_ in commands][-6:]+[seq for seq,_ in handoffs][-2:]}
 return metrics

def assess(c,tid):
 task=c.execute('SELECT status,execution_plan,verification FROM tasks WHERE id=?',(tid,)).fetchone()
 if not task:return {}
 plan=json.loads(task['execution_plan']);verification=json.loads(task['verification'] or '{}');verdicts={}
 rows=[dict(r) for r in c.execute('SELECT seq,agent,kind,content FROM messages WHERE task_id=? ORDER BY seq',(tid,))]
 for goal in c.execute('SELECT * FROM team_growth WHERE task_id=?',(tid,)).fetchall():
  metrics=collect_metrics(c,tid,goal['agent_id'],goal['role'],verification,rows)
  rework=metrics['rework'];final_failure=metrics['final_failure']
  base=json.loads(goal['baseline']);previous=base.get('metrics');gains=[k for k in ('evidence','verified','no_rework') if previous and previous.get(k)==0 and metrics[k]==1]
  issues=min(3,rework+int(final_failure));points=-issues if issues else min(3,len(gains)) if previous else 0
  reason=('실제 재작업·실패 근거 '+str(issues)+'건을 확인했습니다.' if issues else '직전 동종 업무 대비 '+', '.join(gains)+' 개선을 실행·인계 기록으로 확인했습니다.' if gains else '정상 수행은 중립입니다. 비교 가능한 이전 근거가 없거나 검증된 개선 차이가 없습니다.')
  value={'goal':goal['goal'],'leader_id':goal['leader_id'],'baseline':base,'current':metrics,'points':points,'reason':reason,'comparison_task_id':base.get('task_id')}
  verdicts[goal['agent_id']]=value
  c.execute("UPDATE team_growth SET metrics=?,state='evaluated',points=?,reason=?,evaluated=? WHERE task_id=? AND agent_id=?",(json.dumps(metrics),points,reason,time.time(),tid,goal['agent_id']))
 return verdicts

def detail(store,aid):
 with store.connect() as c:rows=[dict(r) for r in c.execute('SELECT * FROM team_growth WHERE agent_id=? ORDER BY created DESC LIMIT 12',(aid,))]
 for r in rows:r['baseline']=json.loads(r['baseline']);r['metrics']=json.loads(r['metrics'])
 if not rows:
  from setup_config import team_settings
  agents=team_settings(store)['agents'];actor=next((a for a in agents if a['id']==aid),None);leader=next((a['id'] for a in agents if not a.get('manager_id')),'')
  if actor:
   role=next((r for r in ('developer','reviewer','reporter','planner') if r in actor.get('capabilities',[])),'planner')
   rows=[{'task_id':None,'agent_id':aid,'leader_id':leader,'goal':GOALS[role],'state':'next_task','baseline':{},'metrics':{},'points':0,'reason':'다음 참여 업무에서 리더가 이 목표와 이전 동종 업무 기준을 함께 전달합니다.'}]
 return rows

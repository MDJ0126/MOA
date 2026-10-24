// In-memory DOM and isolated HTTP only; no browser or computer use.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'../runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function post(path,body){const response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify(body)});assert.ok(response.ok,await response.clone().text());return response.json()}
async function main(){
 await post('/api/control',{action:'pause'});
 const setup=await(await fetch(base+'/api/setup')).json();await post('/api/setup/team',{...setup.presets[1],expected_updated:setup.team.updated||0});
 const a=(await post('/api/tasks',{title:'Draft A',mode:'demo'})).id,b=(await post('/api/tasks',{title:'Draft B',mode:'demo'})).id;
 const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:base+'/#home',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
 w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;w.fetch=(path,options)=>fetch(base+path,options);
 const timers=new Set(),set=w.setTimeout.bind(w),clear=w.clearTimeout.bind(w);let closing=false;
 w.setTimeout=(fn,ms,...args)=>{if(closing)return 0;const id=set(()=>{timers.delete(id);fn(...args)},ms);timers.add(id);return id};w.clearTimeout=id=>{timers.delete(id);clear(id)};
 const input=(selector,value,event='input')=>{const node=d.querySelector(selector);assert.ok(node,selector);node.value=value;node.dispatchEvent(new w.Event(event,{bubbles:true}))};
 const click=selector=>{assert.ok(d.querySelector(selector),selector);d.querySelector(selector).click()};
 async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await sleep(40)}throw Error('Draft timeout: '+label+' / '+d.querySelector('#toast').textContent)}
 w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,`const track=fn=>async(...args)=>{window.draftPending++;try{return await fn(...args)}finally{window.draftPending--}};window.draftPending=0;busy=track(busy);poll=track(poll);boot=track(boot);window.draftOpen=openTask;window.draftOlder=loadOlderLogs;window.draftBreakPlan=()=>{const t=state.tasks.find(t=>t.id===activeTask)||workTasks.find(t=>t.id===activeTask);t.execution_plan='{broken';t.execution_plan_unavailable=true;renderModal()};window.draftRestore=refresh;window.draftDemo=()=>{state.demo_only=true;renderHome()};boot();`));
 try{
  await until(()=>d.querySelectorAll('.agent-card').length===2,'boot');
  await w.draftOpen(a);input('#messageText','A 작성 중');input('#messageRecipient','checker','change');click('[data-action="close-modal"]');
  await w.draftOpen(b);assert.equal(d.querySelector('#messageText').value,'');assert.equal(d.querySelector('#messageRecipient').value,'builder');input('#messageText','B 작성 중');click('[data-action="close-modal"]');
  await w.draftOpen(a);assert.equal(d.querySelector('#messageText').value,'A 작성 중');assert.equal(d.querySelector('#messageRecipient').value,'checker');
  const unload=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
  const original=w.fetch;
  {
   let releaseOlder;
   w.fetch=async(path,options)=>{
    const url=new URL(path,base);
    if(url.pathname==='/api/logs'&&url.searchParams.get('task')===a){
     if(url.searchParams.has('before'))return new Promise(resolve=>{releaseOlder=resolve});
     const response=await original(path,options),data=await response.json();return new Response(JSON.stringify({...data,next_cursor:999999}));
    }
    return original(path,options);
   };
   await w.draftOpen(a);const pending=w.draftOlder();await until(()=>releaseOlder,'older page pending');
   click('[data-action="close-modal"]');await w.draftOpen(a);
   assert.equal(d.querySelector('#olderLogs').hidden,false);
   releaseOlder(new Response(JSON.stringify({messages:[{seq:-1,task_id:a,agent:'user',kind:'instruction',content:'STALE_OLDER_PAGE',created:Date.now()/1000,recipient:''}],next_cursor:null})));
   await pending;
   assert.ok(!d.querySelector('#modalLogs').textContent.includes('STALE_OLDER_PAGE'),'older page from a closed view must not enter a reopened task');
   assert.equal(d.querySelector('#olderLogs').hidden,false,'obsolete page must not replace the reopened cursor');
   w.fetch=original;
  }
  function hold(path){let release,arrived=false;const gate=new Promise(resolve=>release=resolve);w.fetch=async(url,options)=>{const response=await original(url,options);if(url===path){arrived=true;await gate}return response};return {release,ready:()=>arrived}}
  let delayed=hold('/api/messages');click('[data-action="send-message"]');await until(delayed.ready,'message held');click('[data-action="close-modal"]');await w.draftOpen(b);assert.equal(d.querySelector('#messageText').value,'B 작성 중');delayed.release();await until(()=>!d.querySelector('[data-action="send-message"]').disabled,'message ack');
  assert.equal(d.querySelector('#messageText').value,'B 작성 중');assert.equal(d.querySelector('#messageReceipt').hidden,true);w.fetch=original;await w.draftOpen(a);assert.equal(d.querySelector('#messageText').value,'');
  let lose=true;const keys=[];w.fetch=async(path,options)=>{if(path==='/api/messages'){keys.push(JSON.parse(options.body).request_key);const response=await original(path,options);if(lose){lose=false;throw Error('draft response lost')}return response}return original(path,options)};
  input('#messageText','A 응답 유실');click('[data-action="send-message"]');await until(()=>d.querySelector('#toast').textContent.includes('draft response lost'),'message response lost');click('[data-action="close-modal"]');await w.draftOpen(b);await w.draftOpen(a);assert.equal(d.querySelector('#messageText').value,'A 응답 유실');click('[data-action="send-message"]');await until(()=>d.querySelector('#messageText').value==='','message replay ack');assert.equal(keys.length,2);assert.equal(keys[0],keys[1]);w.fetch=original;
  const state=await(await fetch(base+'/api/state')).json();assert.equal(state.deliveries.filter(row=>row.sender==='user'&&JSON.parse(row.content).summary==='A 응답 유실').length,1);
  await until(()=>!d.querySelector('[data-action="send-message"]').disabled,'replay settled');
  delayed=hold('/api/messages');input('#messageText','보내는 중인 지시');click('[data-action="send-message"]');await until(delayed.ready,'same task message pending');assert.ok(d.querySelector('#messageReceipt').textContent.includes('전송 확인 중'));
  input('#messageText','새로 작성 중인 지시');delayed.release();await until(()=>!d.querySelector('[data-action="send-message"]').disabled,'same task message receipt');
  assert.equal(d.querySelector('#messageText').value,'새로 작성 중인 지시');assert.ok(d.querySelector('#modalLogs').textContent.includes('보내는 중인 지시'));
  assert.ok([...d.querySelectorAll('.bubble-head b')].some(el=>el.textContent.includes('→')));input('#messageText','');w.fetch=original;
  let failMessageView=false,messageReceiptPosts=0;
  w.fetch=async(path,options)=>{if(path==='/api/messages'){messageReceiptPosts++;const response=await original(path,options);failMessageView=true;return response}if(path==='/api/state'&&failMessageView)return new Response(JSON.stringify({error:'fixture message state failure'}),{status:503});return original(path,options)};
  input('#messageText','저장은 성공한 지시');click('[data-action="send-message"]');await until(()=>!d.querySelector('[data-action="send-message"]').disabled,'ack and state failure settled');
  assert.ok(d.querySelector('#toast').textContent.includes('지시 저장은 완료'),'acknowledged instruction must be distinguished from view failure');
  assert.equal(d.querySelector('#messageText').value,'');assert.equal(messageReceiptPosts,1);assert.ok(d.querySelector('#messageReceipt').textContent.includes('갱신 확인 필요'));
  input('#messageText','다음 지시 초안');assert.ok(d.querySelector('#messageReceipt').textContent.includes('미전송'));
  click('[data-action="refresh-message-receipt"]');await until(()=>w.draftPending===0,'failed receipt refresh settled');assert.equal(messageReceiptPosts,1);assert.equal(d.querySelector('#messageText').value,'다음 지시 초안');
  failMessageView=false;await until(()=>w.draftPending===0,'receipt actions drained');
  let releaseReceiptLog,receiptLogReady=false;const receiptLogGate=new Promise(resolve=>releaseReceiptLog=resolve);
  w.fetch=async(path,options)=>{if(path==='/api/messages')messageReceiptPosts++;const response=await original(path,options);if(path==='/api/logs?task='+a&&!receiptLogReady){receiptLogReady=true;const older=await response.json();older.messages.push({seq:999999,task_id:a,agent:'system',kind:'control',content:'STALE_RECEIPT_VIEW',created:Date.now()/1000,recipient:'',capability:''});await receiptLogGate;return new Response(JSON.stringify(older))}return response};
  click('[data-action="refresh-message-receipt"]');await until(()=>receiptLogReady,'receipt log lookup held');
  await w.draftOpen(b);assert.equal(d.querySelector('#messageReceipt').hidden,true);assert.equal(d.querySelector('#messageText').value,'B 작성 중');await w.draftOpen(a);
  releaseReceiptLog();await until(()=>w.draftPending===0,'return-to-task receipt lookup settled');
  assert.ok(!d.querySelector('#modalLogs').textContent.includes('STALE_RECEIPT_VIEW'),'older response must not overwrite reopened task');w.fetch=original;
  assert.ok(!d.querySelector('#messageReceipt').textContent.includes('갱신 확인 필요'));assert.equal(messageReceiptPosts,1);assert.equal(d.querySelector('#messageText').value,'다음 지시 초안');input('#messageText','');w.fetch=original;
  click('[data-action="close-modal"]');input('#taskTitle','전송할 작업');input('#taskMode','demo','change');delayed=hold('/api/tasks');click('#submitTask');await until(delayed.ready,'task held');input('#taskTitle','다음 작업 초안');input('#requiredFiles','next.py');delayed.release();await until(()=>!d.querySelector('#submitTask').disabled,'task ack');assert.equal(d.querySelector('#taskTitle').value,'다음 작업 초안');assert.equal(d.querySelector('#requiredFiles').value,'next.py');assert.equal(d.querySelector('#taskModal').hidden,true);w.fetch=original;input('#taskTitle','');input('#requiredFiles','');
  w.location.hash='settings/team';await until(()=>!d.querySelector('#panel-team').hidden,'team tab');input('#agent-name-0','저장 이름');delayed=hold('/api/setup/team');click('#saveTeam');await until(delayed.ready,'team held');input('#agent-name-0','다음 이름');input('[data-connection-field="bot_token"]','xoxb-unsaved-draft');delayed.release();await until(()=>!d.querySelector('#saveTeam').disabled,'team ack');assert.equal(d.querySelector('#agent-name-0').value,'다음 이름');assert.ok(d.querySelector('#teamSaveState').textContent.includes('저장하지'));assert.equal(d.querySelector('[data-connection-field="bot_token"]').value,'xoxb-unsaved-draft');assert.equal((await(await fetch(base+'/api/setup')).json()).team.agents[0].name,'저장 이름');w.fetch=original;
  click('#saveTeam');await until(()=>d.querySelector('#teamSaveState').textContent==='저장됨','new team draft saved');input('[data-connection-field="bot_token"]','');
  w.location.hash='settings/environment';await until(()=>!d.querySelector('#panel-environment').hidden,'environment tab');input('#vmMemory','1024','change');delayed=hold('/api/setup/vm');click('[data-action="save-vm"]');await until(delayed.ready,'VM settings held');input('#vmMemory','2048','change');delayed.release();await until(()=>!d.querySelector('[data-action="save-vm"]').disabled,'VM settings ack');assert.equal(d.querySelector('#vmMemory').value,'2048');assert.ok(d.querySelector('#vmSaveState').textContent.includes('저장하지'));assert.equal((await(await fetch(base+'/api/vm')).json()).config.memory_mb,1024);w.fetch=original;
  click('[data-action="save-vm"]');await until(()=>!d.querySelector('#vmSaveState').textContent,'new VM draft saved');assert.equal((await(await fetch(base+'/api/vm')).json()).config.memory_mb,2048);
  await w.draftOpen(b);assert.equal(d.querySelector('#messageText').value,'B 작성 중');input('#messageText','');click('[data-action="close-modal"]');
  const clean=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(clean);assert.equal(clean.defaultPrevented,false);
  const beforeUnload=()=>{const e=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(e);return e.defaultPrevented};
  for(const [field,value] of [['requiredFiles','only.py'],['acceptance','assert True'],['vmActions','{invalid JSON']]){input('#'+field,value);assert.equal(beforeUnload(),true,field+' alone is an unsent draft');input('#'+field,'');assert.equal(beforeUnload(),false,field+' cleared')}
  input('#taskMode','demo','change');input('#taskTitle','Advanced conditions accepted');input('#requiredFiles','demo-result.md');input('#acceptance','assert True');input('#vmActions','[]');
  const captureTask=w.fetch;let acceptedId=null,lostAdvanced=true,advancedRequests=[];
  w.fetch=async(path,options)=>{const response=await captureTask(path,options);if(path==='/api/tasks'){advancedRequests.push(JSON.parse(options.body));const copy=await response.clone().json();acceptedId=copy.id;if(lostAdvanced){lostAdvanced=false;throw Error('advanced acknowledgement lost')}}return response};
  click('#submitTask');await until(()=>!d.querySelector('#submitTask').disabled&&d.querySelector('#toast').textContent.includes('advanced acknowledgement lost'),'lost advanced acknowledgement');
  assert.equal(d.querySelector('#requiredFiles').value,'demo-result.md');assert.equal(d.querySelector('#acceptance').value,'assert True');assert.equal(d.querySelector('#vmActions').value,'[]');assert.equal(beforeUnload(),true);
  const firstAccepted=acceptedId;click('#submitTask');await until(()=>!d.querySelector('#submitTask').disabled&&d.querySelector('#taskTitle').value==='','advanced acknowledgement retry');
  assert.equal(acceptedId,firstAccepted);assert.equal(advancedRequests[0].request_key,advancedRequests[1].request_key);assert.equal(d.querySelector('#taskMode').value,'demo');assert.equal(d.querySelector('#taskEnvironment').value,'local');for(const field of ['requiredFiles','acceptance','vmActions'])assert.equal(d.querySelector('#'+field).value,'');assert.equal(d.querySelector('#taskOptionsSummary').textContent,'');assert.equal(beforeUnload(),false);w.fetch=original;
  input('#taskTitle','Preserve advanced edits');input('#vmActions','[]');delayed=hold('/api/tasks');click('#submitTask');await until(delayed.ready,'raw advanced edit held');input('#vmActions','[ ]');delayed.release();await until(()=>!d.querySelector('#submitTask').disabled,'raw advanced edit ack');assert.equal(d.querySelector('#taskTitle').value,'Preserve advanced edits');assert.equal(d.querySelector('#vmActions').value,'[ ]');assert.equal(beforeUnload(),true);w.fetch=original;input('#taskTitle','');input('#vmActions','');assert.equal(beforeUnload(),false);

  w.location.hash='';await until(()=>!d.querySelector('#page-home').hidden,'home without hash');input('#taskTitle','주소 해시 없는 첫 작업');input('#taskMode','demo','change');click('#submitTask');await until(()=>!d.querySelector('#taskModal').hidden&&d.querySelector('#modalTitle').textContent.includes('주소 해시 없는 첫 작업'),'new task modal without hash');assert.ok(d.querySelector('#modalTitle').textContent.includes('주소 해시 없는 첫 작업'));click('[data-action="close-modal"]');
  let ackThenStateFailure=false,receiptTaskId=null,receiptPosts=0;
  w.fetch=async(path,options)=>{if(path==='/api/tasks'){receiptPosts++;const response=await original(path,options);receiptTaskId=(await response.clone().json()).id;ackThenStateFailure=true;return response}if(path==='/api/state'&&ackThenStateFailure)return new Response(JSON.stringify({error:'state lookup fixture failure'}),{status:503,headers:{'Content-Type':'application/json'}});return original(path,options)};
  input('#taskTitle','Accepted despite list failure');click('#submitTask');await until(()=>!d.querySelector('#submitTask').disabled&&d.querySelector('#taskReceipt').textContent.includes('화면 갱신 확인 필요'),'ack distinguished from list failure');
  assert.equal(receiptPosts,1);assert.ok(d.querySelector('#taskReceipt').textContent.includes(receiptTaskId));assert.equal(d.querySelector('#taskTitle').value,'');assert.equal(d.querySelector('#taskModal').hidden,true);assert.ok(d.querySelector('#toast').textContent.includes('접수는 완료'));w.fetch=original;
  click('[data-action="find-receipt"]');await until(()=>!d.querySelector('#page-work').hidden&&d.querySelectorAll('#allTasks [data-task]').length===1,'receipt finds accepted task');assert.equal(d.querySelector('#taskSearch').value,receiptTaskId);assert.equal(d.querySelector('#allTasks [data-task]').dataset.task,receiptTaskId);assert.ok(!d.querySelector('#taskReceipt').textContent.includes('확인 필요'));
  click('#allTasks [data-task]');await until(()=>d.querySelector('#modalTitle').textContent==='Accepted despite list failure','accepted task can be reopened');click('[data-action="close-modal"]');
  w.location.hash='home';await until(()=>!d.querySelector('#page-home').hidden,'home for failed detail');
  let failedDetails=false;w.fetch=async(path,options)=>{if(path==='/api/tasks'){const response=await original(path,options);failedDetails=true;return response}if(failedDetails&&path.startsWith('/api/logs?'))return new Response(JSON.stringify({error:'detail lookup fixture failure'}),{status:503,headers:{'Content-Type':'application/json'}});return original(path,options)};
  input('#taskTitle','Accepted despite detail failure');click('#submitTask');await until(()=>!d.querySelector('#submitTask').disabled&&d.querySelector('#taskReceipt').textContent.includes('상세 조회 확인 필요'),'ack distinguished from detail failure');assert.equal(d.querySelector('#taskModal').hidden,true);assert.equal(d.querySelector('#taskTitle').value,'');w.fetch=original;
  w.location.hash='settings/slack';await until(()=>!d.querySelector('#panel-slack').hidden,'Slack tab');
  input('#slackChannel','C12345678');input('#slackUsers','U12345678');input('[data-connection-field="bot_token"]','xoxb-local-fixture');input('[data-connection-field="app_token"]','xapp-local-fixture');
  let releaseAuth,authReady=false,slackWrites=0;const authGate=new Promise(resolve=>releaseAuth=resolve);
  w.fetch=async(path,options)=>{if(path==='/api/slack/test'){authReady=true;await authGate;return new Response(JSON.stringify({checks:[{workspace:'Local fixture',channel:'local'}]}),{headers:{'Content-Type':'application/json'}})}if(path==='/api/setup/slack'){slackWrites++;throw Error('changed authentication must not save')}return original(path,options)};
  click('[data-action="connect-slack"]');await until(()=>authReady,'Slack auth held');input('#slackChannel','C22222222');releaseAuth();await until(()=>!d.querySelector('[data-action="connect-slack"]').disabled,'Slack auth completed');assert.equal(slackWrites,0);assert.ok(d.querySelector('#toast').textContent.includes('연결 정보가 바뀌었습니다'));assert.equal(d.querySelector('#slackChannel').value,'C22222222');
  let releaseSave,slackReady=false;const slackGate=new Promise(resolve=>releaseSave=resolve);
  w.fetch=async(path,options)=>{if(path==='/api/slack/test')return new Response(JSON.stringify({checks:[{workspace:'Local fixture',channel:'local'}]}),{headers:{'Content-Type':'application/json'}});if(path==='/api/setup/slack'){slackWrites++;const body=JSON.parse(options.body);slackReady=true;await slackGate;return new Response(JSON.stringify({...body,updated:99,connections:body.connections.map(c=>({id:c.id,agent_id:c.agent_id,has_bot_token:true,has_app_token:true}))}),{headers:{'Content-Type':'application/json'}})}return original(path,options)};
  click('[data-action="connect-slack"]');await until(()=>slackReady,'Slack save held');input('#slackUsers','U22222222');input('[data-connection-field="bot_token"]','xoxb-next-draft');releaseSave();await until(()=>!d.querySelector('[data-action="connect-slack"]').disabled,'Slack saved');assert.equal(slackWrites,1);assert.equal(d.querySelector('#slackUsers').value,'U22222222');assert.equal(d.querySelector('[data-connection-field="bot_token"]').value,'xoxb-next-draft');assert.equal(d.querySelector('[data-connection-field="app_token"]').value,'');assert.ok(d.querySelector('#slackSaveState').textContent.includes('저장하지'));assert.equal((await(await fetch(base+'/api/setup')).json()).slack.enabled,false);w.fetch=original;
  w.fetch=original;await w.draftOpen(a);input('#messageText','손상 상태에서도 초안 보존');w.draftBreakPlan();assert.ok(d.querySelector('#modalMeta').textContent.includes('기록만 표시'));assert.equal(d.querySelector('#messageText').disabled,true);assert.equal(d.querySelector('[data-action="send-message"]').disabled,true);assert.equal(d.querySelector('[data-action="retry-task"]'),null);assert.ok(d.querySelector('#modalLogs').textContent);await w.draftRestore();assert.equal(d.querySelector('#messageText').disabled,false);assert.equal(d.querySelector('#messageText').value,'손상 상태에서도 초안 보존');click('[data-action="close-modal"]');
  w.draftDemo();assert.equal(d.querySelector('#taskMode').value,'demo');assert.equal(d.querySelector('#taskMode').disabled,true);assert.equal(d.querySelector('#taskEnvironment').disabled,true);assert.equal(d.querySelector('[data-action="connect-slack"]').disabled,true);assert.equal(d.querySelector('[data-action="start-vm"]').disabled,true);assert.ok(d.querySelector('#modelNote').textContent.includes('데모 전용'));
  console.log('PASS: per-task message drafts, recipient isolation, cross-task delayed ack/replay, pending task/team/VM edits, Slack draft preservation, unload guard, demo profile');
 }finally{closing=true;for(const id of timers)clear(id);timers.clear();await until(()=>w.draftPending===0,'pending actions drained');dom.window.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1});

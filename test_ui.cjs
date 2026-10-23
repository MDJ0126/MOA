// Real DOM + local HTTP tests. No browser or computer-use session is opened.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'./runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2];
const dom=new JSDOM(fs.readFileSync('dashboard.html','utf8'),{url:base+'/#home',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document;w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;
const requests=[];let slackSaved=null,setupFailures=1,docFailure=false,loseTaskResponse=true,exported=null;
w.URL.createObjectURL=blob=>{exported=blob;return 'blob:unit-test'};w.URL.revokeObjectURL=()=>{};
w.HTMLAnchorElement.prototype.click=function(){this.dataset.clicked='true'};
w.fetch=async(path,options={})=>{
 const body=options.body?JSON.parse(options.body):null;requests.push({path,body});
 if(path==='/api/setup'&&setupFailures-->0)throw Error('Simulated startup offline');
 if(path==='/api/setup'){const value=await(await fetch(base+path,options)).json();value.hardware={...value.hardware,cpu:'',logical_cpus:null,ram_gib:null};return new Response(JSON.stringify(value))}
 if(path==='/api/docs/slack'&&docFailure)return new Response('{}',{status:503});
 if(path==='/api/tasks'&&loseTaskResponse){loseTaskResponse=false;const response=await fetch(base+path,options);await response.arrayBuffer();throw Error('Simulated response lost after commit')}
 if(path==='/api/slack/test')return new Response(JSON.stringify({ok:true,checks:[{workspace:'UI test workspace',channel:'agent-room'}]}),{headers:{'Content-Type':'application/json'}});
 if(path==='/api/setup/slack'){slackSaved=body;return new Response(JSON.stringify({...body,connections:body.connections.map(c=>({id:c.id,agent_id:c.agent_id,has_bot_token:true,has_app_token:true}))}),{headers:{'Content-Type':'application/json'}})}
 return fetch(base+path,options);
};
w.eval(fs.readFileSync('ui.js','utf8')+'\nwindow.waitRow=taskRow;window.waitNotice=retryNotice;window.testAPI=api;window.testBackupRecovery=recoverBackupRequest;window.testRefresh=refresh;window.testVMStatus=renderVMStatus;window.testLoadTasks=loadTasks;window.testOpenTask=openTask;window.testLoadArtifacts=loadArtifacts;window.testLoadDoc=loadDoc;window.testResetDocs=()=>{docs=[]};');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,description){for(let i=0;i<150;i++){if(fn())return;await sleep(50)}throw Error('Timed out: '+description+' | '+d.querySelector('#toast').textContent)}
function click(selector){const node=d.querySelector(selector);assert.ok(node,selector);node.click()}
function input(selector,value,event='input'){const node=d.querySelector(selector);assert.ok(node,selector);node.value=value;node.dispatchEvent(new w.Event(event,{bubbles:true}))}
async function main(){
 await until(()=>d.querySelectorAll('.agent-card').length===4,'initial team');
 assert.equal(d.querySelector('#machineCpu').textContent,'CPU 정보 미확인');
 assert.equal(d.querySelector('#hardwareSummary').textContent,'CPU 정보 미확인');
 w.testVMStatus({state:'unreachable',owner:null,owner_unavailable:true,error:'fixture owner record unavailable'});
 assert.equal(d.querySelector('#vmOwner').textContent,'사용권 기록 확인 필요');
 assert.equal(d.querySelector('[data-action="capture-vm"]').disabled,true);
 await w.testRefresh();
 assert.equal(d.querySelector('#onboarding').hidden,false);
 const waiting={id:'wait-check',title:'waiting',mode:'demo',status:'queued',wait_reason:'vm_access',stage:0,created:Date.now()/1000,retry_at:Date.now()/1000+1};assert.ok(w.waitRow(waiting).includes('데모'));assert.ok(w.waitRow(waiting).includes('VM 사용권 대기'));assert.ok(w.waitNotice(waiting).includes('VM 사용권 대기'));
 assert.equal(d.querySelector('#profileOS').value,'auto');
 input('#profileOS','macos','change');input('#profileLocation','remote','change');input('#profileBudget','25','change');
 await until(()=>d.querySelector('#profileRecommendation').textContent.includes('원격 장비 사양 미확인'),'remote uses no host budget');
 assert.ok(d.querySelector('#profileSaveState').textContent.includes('저장하지'));
 click('[data-action="save-profile"]');
 await until(()=>d.querySelector('#profileSaveState').textContent==='','profile saved');
 const profileState=await(await fetch(base+'/api/setup')).json();
 assert.equal(profileState.execution_profile.target_os,'macos');assert.equal(profileState.execution_profile.budget_percent,25);
 assert.equal(profileState.environment_recommendation.recommended,null);assert.equal(profileState.environment_recommendation.connected,false);
 const stale=await fetch(base+'/api/setup/execution-profile',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify({expected_updated:0})});assert.equal(stale.status,409);
 click('[data-action="preview-profile"]');await until(()=>d.querySelector('#profileRuntime').textContent.includes('미구현'),'unconnected runtime clearly labeled');
 {const realFetch=w.fetch;let releaseOld,previewCount=0;
 w.fetch=(path,options)=>{if(path==='/api/setup/execution-preview'){const result={...profileState.environment_recommendation,note:'최신 추천'};if(previewCount++===0)return new Promise(resolve=>{releaseOld=()=>resolve(new Response(JSON.stringify({...result,note:'이전 추천'})))});return Promise.resolve(new Response(JSON.stringify(result)))}return realFetch(path,options)};
 input('#profileBudget','50','change');input('#profileBudget','25','change');
 await until(()=>d.querySelector('#profileRecommendation').textContent.includes('최신 추천'),'latest preview wins');releaseOld();await sleep(50);
 assert.ok(d.querySelector('#profileRecommendation').textContent.includes('최신 추천'));assert.equal(d.querySelector('#profileBudget').value,'25');w.fetch=realFetch;}


 await until(()=>d.querySelector('#taskProfile').textContent.includes('데모'),'server default mode shown');
 assert.equal(d.querySelector('#taskMode').value,'demo');assert.equal(d.querySelector('#taskOptions').open,false);
 input('#taskMode','ollama','change');await w.testRefresh();assert.equal(d.querySelector('#taskMode').value,'ollama');assert.ok(d.querySelector('#taskProfile').textContent.includes('Ollama'));
 d.querySelector('#taskOptions').open=true;input('#taskEnvironment','vm','change');input('#requiredFiles','one.py\ntwo.py');input('#acceptance','assert True');
 d.querySelector('#taskOptions').open=false;assert.ok(d.querySelector('#taskProfile').textContent.includes('공유 VM'));assert.ok(d.querySelector('#taskOptionsSummary').textContent.includes('파일 2개'));assert.ok(d.querySelector('#taskOptionsSummary').textContent.includes('고정 검증'));assert.equal(d.querySelector('#requiredFiles').value,'one.py\ntwo.py');
 input('#requiredFiles','');input('#acceptance','');input('#taskEnvironment','local','change');input('#taskMode','demo','change');assert.equal(d.querySelector('#taskOptionsSummary').textContent,'');

 click('[data-action="recommended"]');await until(()=>!d.querySelector('#page-settings').hidden,'settings navigation');
 assert.equal(d.querySelectorAll('.agent-editor').length,2);
 input('#agent-name-0','저장 전 이름');assert.ok(d.querySelector('#teamSaveState').textContent.includes('저장하지'));
 click('[data-action="reset-team"]');assert.equal(d.querySelector('#agent-name-0').value,'계획 담당');
 click('[data-preset="light"]');
 input('#agent-name-0','테스트 빌더');click('#saveTeam');
 await until(()=>d.querySelector('#toast').textContent.includes('팀 구성을 저장했습니다'),'team save');
 const config=await(await fetch(base+'/api/setup')).json();assert.equal(config.team.agents.length,2);assert.equal(config.team.agents[0].name,'테스트 빌더');
 const external=structuredClone(config.team);external.agents[0].name='다른 창의 이름';external.expected_updated=config.team.updated;
 await fetch(base+'/api/setup/team',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify(external)});
 input('#agent-name-0','유지할 입력');click('#saveTeam');await until(()=>!d.querySelector('#settingsConflict').hidden,'stale configuration conflict');
 assert.equal(d.querySelector('#agent-name-0').value,'유지할 입력');
 click('[data-action="reload-settings"]');await until(()=>d.querySelector('#toast').textContent.includes('최신 저장 버전'),'refresh version without losing draft');
 assert.equal(d.querySelector('#agent-name-0').value,'유지할 입력');click('#saveTeam');await until(()=>d.querySelector('#toast').textContent.includes('팀 구성을 저장'),'save after reviewing conflict');
 click('[data-preset="compact"]');click('#saveTeam');
 await until(()=>d.querySelectorAll('.agent-card').length===1,'compact saved');
 w.location.hash='home';await until(()=>!d.querySelector('#page-home').hidden,'home navigation');
 input('#taskTitle','DOM 시험: <script>alert(1)</script> 작업');input('#taskMode','demo','change');click('#submitTask');
 await until(()=>d.querySelector('#toast').textContent.includes('response lost'),'unknown submission outcome');
 assert.ok(d.querySelector('#taskTitle').value.includes('DOM 시험'));click('#submitTask');
 await until(()=>!d.querySelector('#taskModal').hidden,'task modal');
 await until(()=>d.querySelector('#modalMeta').textContent.includes('완료'),'actual demo completed');
 assert.ok(d.querySelector('#modalTitle').textContent.includes('<script>'));
 assert.equal(d.querySelectorAll('#modalTitle script').length,0);
 await until(()=>d.querySelector('a[href*="artifacts.zip"]'),'available artifact links');
 const state=await(await fetch(base+'/api/state')).json();assert.equal(state.tasks[0].stage,2);
 assert.ok(state.messages.some(m=>m.agent==='maker'&&m.kind==='start'));
 click('[data-action="technical"]');assert.ok(d.querySelectorAll('.bubble-body.technical').length>0);
 input('#messageText','완료 작업 추가 지시');click('[data-action="send-message"]');
 await until(()=>d.querySelector('#toast').textContent.includes('완료·취소'),'completed instruction rejected');
 assert.equal(d.querySelector('#messageText').value,'완료 작업 추가 지시');
 const afterRejectedMessage=await(await fetch(base+'/api/state')).json();assert.equal(afterRejectedMessage.deliveries.filter(row=>row.sender==='user').length,0);
 input('#messageText','');input('#logAgent','maker','change');
 assert.ok([...d.querySelectorAll('.bubble-head b')].some(el=>el.textContent.includes('개발')));input('#logAgent','','change');
 const retryFetch=w.fetch,retryState=await(await fetch(base+'/api/state')).json();retryState.tasks[0]={...retryState.tasks[0],status:'queued',error:'모델 서버 일시 오류',retry_at:Date.now()/1000+45};
 w.fetch=async(path,options)=>path==='/api/state'?new Response(JSON.stringify(retryState),{headers:{'Content-Type':'application/json'}}):retryFetch(path,options);
 await w.testRefresh();assert.ok(d.querySelector('#modalLogs').textContent.includes('자동 재시도 대기'));
 retryState.paused=true;await w.testRefresh();assert.ok(d.querySelector('#modalLogs').textContent.includes('일시 중지 해제 후'));
 w.fetch=retryFetch;await w.testRefresh();
 const artifactFetch=w.fetch;
 w.fetch=async(path,options)=>path.startsWith('/api/artifacts?')?new Response(JSON.stringify({task_id:state.tasks[0].id,files:[]}),{headers:{'Content-Type':'application/json'}}):artifactFetch(path,options);
 await w.testRefresh();assert.equal(d.querySelector('a[href*="file=artifacts.zip"]'),null);
 assert.ok(d.querySelector('#modalActions').textContent.includes('결과 파일이 없습니다'));
 w.fetch=async(path,options)=>path.startsWith('/api/artifacts?')?new Response(JSON.stringify({error:'결과 파일 조회 실패'}),{status:503,headers:{'Content-Type':'application/json'}}):artifactFetch(path,options);
 await w.testRefresh();assert.ok(d.querySelector('[data-action="refresh-artifacts"]'));
 w.fetch=artifactFetch;click('[data-action="refresh-artifacts"]');await until(()=>d.querySelector('a[href*="file=artifacts.zip"]'),'artifact lookup retry');
 {
 const viewFetch=w.fetch,tid=state.tasks[0].id;let releaseOld,artifactCalls=0;
 w.fetch=(path,options)=>{if(path.startsWith('/api/artifacts?')){if(artifactCalls++===0)return new Promise(resolve=>{releaseOld=resolve});return Promise.resolve(new Response(JSON.stringify({task_id:tid,files:[{name:'artifacts.zip'}]})))}return viewFetch(path,options)};
 const pending=w.testLoadArtifacts(tid);await until(()=>releaseOld,'older artifact listing pending');
 click('[data-action="close-modal"]');await w.testOpenTask(tid);
 assert.ok(d.querySelector('a[href*="file=artifacts.zip"]'));
 releaseOld(new Response(JSON.stringify({task_id:tid,files:[]})));await pending;
 assert.ok(d.querySelector('a[href*="file=artifacts.zip"]'),'old artifact listing must not overwrite reopened results');w.fetch=viewFetch;
 const vmState=structuredClone(state),task=vmState.tasks.find(task=>task.id===tid),taskPlan=JSON.parse(task.execution_plan);taskPlan.environment={kind:'vm'};task.execution_plan=JSON.stringify(taskPlan);
 let releaseVM,vmCalls=0;
 w.fetch=(path,options)=>{if(path==='/api/state')return Promise.resolve(new Response(JSON.stringify(vmState)));if(path.startsWith('/api/vm/evidence?')){if(vmCalls++===0)return new Promise(resolve=>{releaseVM=resolve});return Promise.resolve(new Response(JSON.stringify({files:['vm/latest-view.png']})))}return viewFetch(path,options)};
 const olderRefresh=w.testRefresh();await until(()=>releaseVM,'older saved VM evidence pending');
 click('[data-action="close-modal"]');await w.testOpenTask(tid);
 assert.ok(d.querySelector('#vmEvidence').textContent.includes('latest-view.png'));
 releaseVM(new Response(JSON.stringify({files:['vm/obsolete-view.png']})));await olderRefresh;
 assert.ok(d.querySelector('#vmEvidence').textContent.includes('latest-view.png'),'old evidence must not overwrite reopened VM records');
 assert.ok(!d.querySelector('#vmEvidence').textContent.includes('obsolete-view.png'));w.fetch=viewFetch;await w.testRefresh();
 }
 click('[data-action="export-logs"]');await until(()=>d.querySelector('#toast').textContent.includes('내보냈습니다'),'conversation export');assert.ok(exported);
 const taskRequests=requests.filter(r=>r.path==='/api/tasks');assert.equal(taskRequests.length,2);assert.equal(taskRequests[0].body.request_key,taskRequests[1].body.request_key);
 click('[data-action="close-modal"]');
 w.location.hash='work';await until(()=>!d.querySelector('#page-work').hidden&&d.querySelectorAll('#allTasks [data-task]').length===1,'server task list');
 input('#taskSearch','없는 제목');await until(()=>d.querySelector('#workCount').textContent==='0개 작업','server search empty');
 input('#taskSearch','DOM 시험');await until(()=>d.querySelector('#workCount').textContent==='1개 작업','server search found');
 {
 const beforeSearchFetch=w.fetch,searchInput=d.querySelector('#taskSearch'),originalSearch=searchInput.value;
 const latest=await(await fetch(base+'/api/tasks?page=0&status=all&q='+encodeURIComponent(originalSearch))).json();
 for(const failOld of [false,true]){
  let releaseOld,rejectOld,calls=0;
  w.fetch=(path,options)=>{if(path.startsWith('/api/tasks?')){if(calls++===0)return new Promise((resolve,reject)=>{releaseOld=resolve;rejectOld=reject});return Promise.resolve(new Response(JSON.stringify(latest)))}return beforeSearchFetch(path,options)};
  searchInput.value='older search';const old=w.testLoadTasks();await until(()=>releaseOld,'older task search pending');
  searchInput.value=originalSearch;await w.testLoadTasks();assert.equal(d.querySelector('#workCount').textContent,'1개 작업');
  if(failOld)rejectOld(Error('OLD_SEARCH_FAILURE'));else releaseOld(new Response(JSON.stringify({tasks:[],total:0,has_more:false})));
  await assert.doesNotReject(old,'obsolete search failures must not report a current failure');
  assert.equal(d.querySelector('#workCount').textContent,'1개 작업');
  assert.equal(d.querySelectorAll('#allTasks [data-task]').length,1);
 }
 w.fetch=(path,options)=>path.startsWith('/api/tasks?')?Promise.resolve(new Response(JSON.stringify({error:'LATEST_SEARCH_FAILURE'}),{status:503})):beforeSearchFetch(path,options);
 await assert.rejects(w.testLoadTasks(),/LATEST_SEARCH_FAILURE/,'latest search failures must remain visible');
 w.fetch=beforeSearchFetch;searchInput.value=originalSearch;
 }
 assert.ok(d.querySelector('.sidebar [data-page="work"]').getAttribute('aria-label'));
 w.location.hash='settings/slack';await until(()=>!d.querySelector('#panel-slack').hidden,'slack tab');
 d.querySelector('#tab-slack').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));await until(()=>!d.querySelector('#panel-team').hidden,'keyboard tab navigation');
 assert.equal(d.querySelector('#tab-team').tabIndex,0);click('#tab-slack');await until(()=>!d.querySelector('#panel-slack').hidden,'keyboard return');
 input('#slackChannel','C12345678');input('#slackUsers','U12345678');input('[data-connection-field="bot_token"]','xoxb-ui-test');input('[data-connection-field="app_token"]','xapp-ui-test');
 assert.ok(d.querySelector('#slackSaveState').textContent.includes('저장하지 않은'));
 click('[data-action="add-slack"]');assert.equal(d.querySelector('[data-connection-field="bot_token"]').value,'xoxb-ui-test');
 click('[data-remove-connection]');assert.equal(d.querySelector('[data-connection-field="app_token"]').value,'xapp-ui-test');
 click('#testSlack');await until(()=>d.querySelector('#slackTestResult').textContent.includes('확인 완료'),'connection test presentation');
 assert.equal(slackSaved,null,'test must not enable connection');
 click('#connectSlack');await until(()=>slackSaved!==null,'save slack');
 assert.equal(slackSaved.allowed_users[0],'U12345678');assert.equal(slackSaved.enabled,true);
 await until(()=>d.querySelector('[data-connection-field="bot_token"]').value==='','clear saved token field');
 assert.ok(d.querySelector('[data-connection-field="bot_token"]').placeholder.includes('저장된'));
 w.location.hash='settings/environment';await until(()=>!d.querySelector('#panel-environment').hidden,'shared environment tab');
 assert.equal(d.querySelector('#vmAdvanced').open,false);assert.equal(d.querySelector('#vmManualControls').open,false);assert.equal(d.querySelector('#maintenanceOptions').open,false);assert.equal(d.querySelector('#vmMemory').closest('details').id,'vmAdvanced');d.querySelector('#vmAdvanced').open=true;
 assert.equal(d.querySelector('#vmMemory').value,'512');
 input('#vmMemory','1024','change');assert.ok(d.querySelector('#vmSaveState').textContent.includes('저장하지'));
 click('[data-action="save-vm"]');await until(()=>d.querySelector('#toast').textContent.includes('VM 환경을 저장'),'VM configuration save');
 const vmConfig=await(await fetch(base+'/api/vm')).json();assert.equal(vmConfig.config.memory_mb,1024);assert.equal(vmConfig.state,'stopped');
 const externalVM={...vmConfig.config,memory_mb:1536,expected_updated:vmConfig.config.updated};
 const changedVM=await fetch(base+'/api/setup/vm',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify(externalVM)});assert.equal(changedVM.status,200);
 input('#vmMemory','2048','change');click('[data-action="save-vm"]');await until(()=>!d.querySelector('#settingsConflict').hidden,'VM stale version conflict');
 assert.equal(d.querySelector('#vmMemory').value,'2048');
 assert.equal((await(await fetch(base+'/api/vm')).json()).config.memory_mb,1536);
 click('[data-action="reload-settings"]');await until(()=>d.querySelector('#toast').textContent.includes('최신 저장 버전'),'VM latest version without losing draft');
 assert.equal(d.querySelector('#vmMemory').value,'2048');assert.ok(d.querySelector('#vmSaveState').textContent.includes('저장하지'));
 click('[data-action="save-vm"]');await until(()=>d.querySelector('#settingsConflict').hidden,'VM reviewed conflict save');
 assert.equal((await(await fetch(base+'/api/vm')).json()).config.memory_mb,2048);
 assert.deepEqual([...d.querySelector('#taskEnvironment').options].map(option=>option.value),['local','profile','vm']);
 d.querySelector('#maintenanceOptions').open=true;click('[data-action="diagnose"]');await until(()=>d.querySelector('#installationChecks').textContent.includes('Python'),'installation diagnostics');
 await until(()=>d.querySelector('#installationChecks').textContent.includes('서버 응답 확인'),'observed service health');
 assert.ok(d.querySelector('#installationChecks').textContent.includes('서버 변경 적용'));
 const sourceFetch=w.fetch;let sourceControls=0;
 w.fetch=async(path,options)=>{if(path==='/api/control')sourceControls++;if(path==='/api/diagnostics')return new Response(JSON.stringify({checks:[{id:'application_sources',label:'서버 변경 적용',state:'restart',detail:'서버 시작 뒤 파일이 바뀌었습니다.'}],disk_free_gib:10,scope:'fixture'}),{headers:{'Content-Type':'application/json'}});return sourceFetch(path,options)};
 click('[data-action="diagnose"]');await until(()=>d.querySelector('#installationChecks').textContent.includes('재시작 필요'),'restart required diagnostic');assert.equal(sourceControls,0);w.fetch=sourceFetch;

 click('[data-action="list-backups"]');await until(()=>d.querySelector('#backupResult').textContent.includes('개 백업'),'backup list');
 const installationFetch=w.fetch;
 w.fetch=async(path,options)=>path==='/api/backups/inspect'?new Response(JSON.stringify({files:3,tasks:2,messages:7,restored:false}),{headers:{'Content-Type':'application/json'}}):installationFetch(path,options);
 d.querySelector('#backupFile').innerHTML='<option value="test.zip">test.zip</option>';
 click('[data-action="inspect-backup"]');await until(()=>d.querySelector('#backupResult').textContent.includes('작업 2개'),'backup preview');
 assert.ok(d.querySelector('#backupResult').textContent.includes('현재 데이터는 변경하지'));
 w.fetch=async(path,options)=>path==='/api/backups/inspect'?new Response(JSON.stringify({error:'손상된 백업입니다.'}),{status:400,headers:{'Content-Type':'application/json'}}):installationFetch(path,options);
 click('[data-action="inspect-backup"]');await until(()=>d.querySelector('#backupResult').textContent.includes('손상된 백업'),'backup error preservation');
 let backupKey=null,backupPosts=0;
 w.fetch=async(path,options)=>{if(path==='/api/backups/create'){const body=JSON.parse(options.body);if(backupKey)assert.equal(body.request_key,backupKey);backupKey=body.request_key;backupPosts++;if(backupPosts===1)throw Error('simulated lost backup response');return new Response(JSON.stringify({file:'test.zip',state:'waiting',message:'앱 종료 후 백업합니다.'}),{status:202,headers:{'Content-Type':'application/json'}})}if(path.startsWith('/api/backups/request?'))return new Response(JSON.stringify({file:'test.zip',state:'waiting',message:'접수된 백업입니다.'}),{headers:{'Content-Type':'application/json'}});return installationFetch(path,options)};
 click('[data-action="create-backup"]');await until(()=>d.querySelector('#backupCreateState').dataset.accepted==='unknown','lost backup response');
 assert.equal(w.localStorage.getItem('moa.pendingBackup'),backupKey);
 click('[data-action="create-backup"]');await until(()=>d.querySelector('#backupCreateState').dataset.accepted==='true','scheduled backup acknowledgement');
 assert.equal(d.querySelector('[data-action="create-backup"]').hidden,true);
 w.fetch=async()=>new Response(JSON.stringify({file:'test.zip',state:'completed',message:'백업 완료'}),{headers:{'Content-Type':'application/json'}});
 await w.testBackupRecovery();assert.equal(w.localStorage.getItem('moa.pendingBackup'),null);
 delete d.querySelector('#backupCreateState').dataset.accepted;
 w.fetch=installationFetch;
 {const beforeRecoveryFetch=w.fetch;let held=[{kind:'notification',id:12,text:'<img src=x onerror=alert(1)>'},{kind:'instruction',id:'EvReview',text:'검토할 지시'}],recoveryPosts=[];
 w.fetch=async(path,options={})=>{if(path==='/api/slack/recovery'){if(options.method==='POST'){const body=JSON.parse(options.body);recoveryPosts.push(body);held=held.filter(item=>!body.items.some(chosen=>chosen.kind===item.kind&&chosen.id===item.id));return new Response(JSON.stringify({processed:body.items.length,action:body.action}),{headers:{'Content-Type':'application/json'}})}return new Response(JSON.stringify({counts:{notification:held.filter(x=>x.kind==='notification').length,instruction:held.filter(x=>x.kind==='instruction').length},items:held}),{headers:{'Content-Type':'application/json'}})}return beforeRecoveryFetch(path,options)};
 w.location.hash='settings/slack';await until(()=>d.querySelectorAll('[data-recovery-index]').length===2,'restored queue preview');
 assert.equal(d.querySelector('#slackRecoveryItems img'),null);assert.ok(d.querySelector('#slackRecoveryItems').textContent.includes('<img'));
 click('[data-action="resume-slack-recovery"]');await until(()=>d.querySelector('#toast').textContent.includes('선택하세요'),'explicit selection required');assert.equal(recoveryPosts.length,0);
 d.querySelector('[data-recovery-index="0"]').checked=true;click('[data-action="resume-slack-recovery"]');await until(()=>d.querySelectorAll('[data-recovery-index]').length===1,'selective resume');
 assert.deepEqual(recoveryPosts[0],{action:'resume',items:[{kind:'notification',id:12}]});
 d.querySelector('[data-recovery-index="0"]').checked=true;click('[data-action="dismiss-slack-recovery"]');await until(()=>d.querySelector('#slackRecovery').hidden,'dismiss remaining item');
 assert.equal(recoveryPosts[1].action,'dismiss');w.fetch=beforeRecoveryFetch;}
 docFailure=true;w.location.hash='docs/slack';await until(()=>d.querySelector('[data-action="retry-doc"]'),'guide error and retry');
 docFailure=false;click('[data-action="retry-doc"]');
 await until(()=>d.querySelector('#docContent h1')?.textContent==='Slack 연결하기','built-in guide');
 assert.ok(d.querySelectorAll('#docNav a').length>=8);
 {
 const catalog=await(await fetch(base+'/api/docs')).json(),beforeRaceFetch=w.fetch;
 for(const failOld of [false,true]){
  let releaseOld,rejectOld,calls=0;w.testResetDocs();
  w.fetch=(path,options)=>{
   if(path==='/api/docs'){if(calls++===0)return new Promise((resolve,reject)=>{releaseOld=resolve;rejectOld=reject});return Promise.resolve(new Response(JSON.stringify(catalog)))}
   if(path==='/api/docs/team')return Promise.resolve(new Response('<h1>최신 팀 안내</h1>'));
   if(path==='/api/docs/slack')return Promise.resolve(new Response('<h1>이전 Slack 안내</h1>'));
   return beforeRaceFetch(path,options);
  };
  const old=w.testLoadDoc('slack');await until(()=>releaseOld,'older guide catalog pending');
  await w.testLoadDoc('team');assert.equal(d.querySelector('#docContent h1').textContent,'최신 팀 안내');
  if(failOld)rejectOld(Error('old catalog offline'));else releaseOld(new Response(JSON.stringify(catalog)));
  await old;
  assert.equal(d.querySelector('#docNav a.active')?.dataset.doc,'team','stale catalog must not change the active guide');
  assert.equal(d.querySelector('#docContent h1').textContent,'최신 팀 안내');
 }
 w.fetch=beforeRaceFetch;
 }
 input('#docSearch','메모리');assert.ok(d.querySelectorAll('#docNav a').length<8);
 const originalFetch=w.fetch,originalTimer=w.setTimeout;
 w.setTimeout=(fn,ms,...args)=>originalTimer(fn,ms===30000?25:ms,...args);
 w.fetch=(path,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError'))));
 await assert.rejects(w.testAPI('/timeout-test'),/응답 시간이 초과/);
 w.fetch=async()=>new Response('not json');await assert.rejects(w.testAPI('/bad-response'),/서버 응답/);
 w.fetch=originalFetch;w.setTimeout=originalTimer;
 console.log('PASS: installation diagnostics, backup list/preview/error/schedule, shared VM settings/tab/dirty/save, startup recovery, request timeout, bad JSON response, dirty state/reset, keyboard tabs, server task search, guide failure/retry, preset/team, compact execution, safe text, modal, Slack distinction/secret clearing, docs/search');
}
main().then(()=>{dom.window.close()}).catch(error=>{console.error(error);dom.window.close();process.exitCode=1});

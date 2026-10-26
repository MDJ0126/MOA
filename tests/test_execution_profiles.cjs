// Actual isolated HTTP + DOM; paused worker, no VM/model/Slack calls.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'../runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function post(path,value){const response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify(value)});assert.ok(response.ok,await response.clone().text());return response.json()}
async function main(){
 await post('/api/control',{action:'pause'});
 const original=(await(await fetch(base+'/api/setup')).json()).execution_profile;
 const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:base+'/#home',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
 w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;w.fetch=(path,options)=>fetch(base+path,options);
 const timers=new Set(),set=w.setTimeout.bind(w),clear=w.clearTimeout.bind(w);let closing=false,taskId;
 w.setTimeout=(fn,ms,...args)=>{if(closing)return 0;const id=set(()=>{timers.delete(id);fn(...args)},ms);timers.add(id);return id};w.clearTimeout=id=>{timers.delete(id);clear(id)};
 function input(id,value){const node=d.querySelector('#'+id);node.value=value;node.dispatchEvent(new w.Event('change',{bubbles:true}))}
 async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await sleep(40)}throw Error(label+' / '+d.querySelector('#toast').textContent)}
 w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,`const track=fn=>async(...args)=>{window.routingPending++;try{return await fn(...args)}finally{window.routingPending--}};window.routingPending=0;busy=track(busy);poll=track(poll);boot=track(boot);boot();`));
 try{
  await until(()=>d.querySelectorAll('.office-station').length>0,'boot');
  input('profileOS','auto');input('profileLocation','host');d.querySelector('[data-action="save-profile"]').click();
  await until(()=>d.querySelector('#toast').textContent.includes('선호 환경을 저장'),'host preference saved');
  input('taskEnvironment','profile');input('taskMode','demo');input('taskTitle','Native profile DOM queued only');
  assert.ok(d.querySelector('#taskProfile').textContent.includes('저장 환경'));
  d.querySelector('#submitTask').click();await until(()=>d.querySelector('#taskReceipt').dataset.taskId,'queued task receipt');
  taskId=d.querySelector('#taskReceipt').dataset.taskId;
  const task=(await(await fetch(base+'/api/tasks?q='+taskId)).json()).tasks[0],plan=JSON.parse(task.execution_plan);
  assert.equal(task.status,'queued');assert.equal(plan.environment.kind,'local');assert.equal(plan.environment.execution_contract.runner,'host');
  assert.equal(plan.environment.execution_contract.requested.location,'host');assert.ok(['windows','linux','macos'].includes(plan.environment.execution_contract.target_os));
  await until(()=>!d.querySelector('#submitTask').disabled,'submission drained');
  if(!d.querySelector('#taskModal').hidden)d.querySelector('[data-action="close-modal"]').click();
  await post('/api/control',{action:'cancel',id:taskId});taskId=null;
  input('profileLocation','remote');d.querySelector('[data-action="save-profile"]').click();
  await until(()=>!d.querySelector('[data-action="save-profile"]').disabled&&d.querySelector('#profileSaveState').textContent==='','remote preference saved');
  input('taskTitle','Must retain blocked environment draft');d.querySelector('#submitTask').click();
  await until(()=>d.querySelector('#toast').textContent.includes('아직 연결되지'),'remote rejected explicitly');
  assert.equal(d.querySelector('#taskTitle').value,'Must retain blocked environment draft');
  const rejected=(await(await fetch(base+'/api/tasks?q='+encodeURIComponent('Must retain blocked environment draft'))).json()).tasks;
  assert.equal(rejected.length,0);
  console.log('PASS: saved native profile HTTP queue contract, target label, unconnected remote rejection and draft retention; no actual VM/model execution');
 }finally{
  if(taskId)await post('/api/control',{action:'cancel',id:taskId});
  const current=(await(await fetch(base+'/api/setup')).json()).execution_profile;await post('/api/setup/execution-profile',{...original,expected_updated:current.updated});
  closing=true;for(const id of timers)clear(id);timers.clear();await until(()=>w.routingPending===0,'pending actions drained');dom.window.close();
 }
}
main().catch(error=>{console.error(error);process.exitCode=1});

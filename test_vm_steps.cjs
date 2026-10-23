// Selection editor + actual isolated HTTP; worker stays paused, no VM is started.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'./runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function post(path,value){const response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify(value)});assert.ok(response.ok,await response.clone().text());return response.json()}
async function main(){
 await post('/api/control',{action:'pause'});
 const before=(await(await fetch(base+'/api/vm')).json()).config;
 await post('/api/setup/vm',{...before,enabled:true,expected_updated:before.updated||0});
 const dom=new JSDOM(fs.readFileSync('dashboard.html','utf8'),{url:base+'/#home',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
 w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;w.fetch=(path,options)=>fetch(base+path,options);
 const timers=new Set(),set=w.setTimeout.bind(w),clear=w.clearTimeout.bind(w);let closing=false;
 w.setTimeout=(fn,ms,...args)=>{if(closing)return 0;const id=set(()=>{timers.delete(id);fn(...args)},ms);timers.add(id);return id};w.clearTimeout=id=>{timers.delete(id);clear(id)};
 const input=(selector,value,event='input')=>{const node=d.querySelector(selector);assert.ok(node,selector);node.value=value;node.dispatchEvent(new w.Event(event,{bubbles:true}))};
 const click=selector=>{assert.ok(d.querySelector(selector),selector);d.querySelector(selector).click()};
 async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await sleep(40)}throw Error('VM editor timeout: '+label+' / '+d.querySelector('#toast').textContent)}
 const raw=()=>JSON.parse(d.querySelector('#vmActions').value||'[]');
 w.eval(fs.readFileSync('ui.js','utf8').replace(/boot\(\);\s*$/,`const track=fn=>async(...args)=>{window.stepPending++;try{return await fn(...args)}finally{window.stepPending--}};window.stepPending=0;busy=track(busy);poll=track(poll);boot=track(boot);window.stepRefresh=refresh;boot();`));
 let taskId;
 try{
  await until(()=>d.querySelectorAll('.agent-card').length>0,'boot');assert.equal(d.querySelector('#vmActionOptions').hidden,true);
  d.querySelector('#taskOptions').open=true;input('#taskEnvironment','vm','change');assert.equal(d.querySelector('#vmActionOptions').hidden,false);d.querySelector('#vmActionOptions').open=true;
  click('[data-action="add-vm-step"]');assert.deepEqual(raw(),[{role:'developer',action:'key',keys:['ret']}]);input('[data-vm-field="keys"]',JSON.stringify(['ctrl','a']),'change');assert.deepEqual(raw()[0].keys,['ctrl','a']);
  input('[data-vm-step="0"][data-vm-field="role"]','reviewer','change');input('[data-vm-step="0"][data-vm-field="action"]','text','change');input('[data-vm-step="0"][data-vm-field="text"]','<b>guest text</b>');assert.equal(d.querySelector('#vmActionSteps b'),null);
  const textNode=d.querySelector('[data-vm-field="text"]');textNode.focus();await w.stepRefresh();assert.equal(d.querySelector('[data-vm-field="text"]'),textNode);
  click('[data-action="add-vm-step"]');input('[data-vm-step="1"][data-vm-field="action"]','click','change');input('[data-vm-field="x"]','25');input('[data-vm-field="y"]','75');input('[data-vm-field="button"]','right','change');assert.equal(raw()[1].x,8192);assert.equal(raw()[1].y,24575);
  click('[data-action="add-vm-step"]');click('[data-vm-move="2"][data-direction="-1"]');assert.equal(raw()[1].action,'key');assert.equal(raw()[2].action,'click');
  click('[data-action="add-vm-step"]');input('[data-vm-step="3"][data-vm-field="action"]','wait','change');input('[data-vm-field="seconds"]','2.5');
  click('[data-action="add-vm-step"]');input('[data-vm-step="4"][data-vm-field="action"]','capture','change');
  assert.deepEqual([...d.querySelectorAll('[data-vm-field="role"]')].map(node=>node.value),['developer','developer','developer','developer','reviewer']);
  input('[data-vm-field="seconds"]','');click('[data-action="add-vm-step"]');assert.equal(d.querySelector('[data-vm-field="seconds"]').value,'');click('[data-vm-remove="5"]');input('[data-vm-field="seconds"]','2.5');
  const valid=d.querySelector('#vmActions').value;
  input('#vmActions','{unfinished');assert.equal(d.querySelector('#vmActions').value,'{unfinished');assert.equal(d.querySelector('#vmActionError').hidden,false);assert.equal(d.querySelector('[data-action="add-vm-step"]').disabled,true);
  input('#vmActions',JSON.stringify(Array.from({length:40},()=>({role:'developer',action:'capture'}))));assert.equal(d.querySelectorAll('.vm-step').length,40);assert.equal(d.querySelector('[data-action="add-vm-step"]').disabled,true);click('[data-vm-remove="0"]');assert.equal(raw().length,39);assert.equal(d.querySelector('[data-action="add-vm-step"]').disabled,false);
  input('#vmActions',valid);const expected=raw();input('#taskTitle','VM selection editor queued only');input('#taskMode','demo','change');click('#submitTask');await until(()=>!d.querySelector('#submitTask').disabled&&d.querySelector('#taskTitle').value==='','actual task accepted');
  taskId=d.querySelector('#taskReceipt').dataset.taskId;const result=await(await fetch(base+'/api/tasks?q='+taskId)).json();const plan=JSON.parse(result.tasks[0].execution_plan);
  assert.equal(result.tasks[0].status,'queued');assert.equal(plan.environment.kind,'vm');assert.deepEqual(plan.environment.actions,expected);assert.equal(d.querySelectorAll('.vm-step').length,0);
  await post('/api/control',{action:'cancel',id:taskId});taskId=null;
  console.log('PASS: VM selection editor, role grouping, key/text/click/wait/capture, percent coordinates, same-role ordering, focus preservation, invalid JSON retention, 40-step limit, paused HTTP plan persistence; no actual VM input');
 }finally{
  if(taskId)await post('/api/control',{action:'cancel',id:taskId});
  const current=(await(await fetch(base+'/api/vm')).json()).config;await post('/api/setup/vm',{...before,expected_updated:current.updated});
  closing=true;for(const id of timers)clear(id);timers.clear();await until(()=>w.stepPending===0,'pending actions drained');dom.window.close();
 }
}
main().catch(error=>{console.error(error);process.exitCode=1});

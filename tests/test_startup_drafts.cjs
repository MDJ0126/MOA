// Startup recovery preserves visible drafts; no browser or VM is opened.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'../runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function scenario(failedPath){
 const dom=new JSDOM(fs.readFileSync('dashboard.html','utf8'),{url:base+'/#home',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,requests=[];let fail=true,retry,inflight=0;
 w.structuredClone=structuredClone;w.AbortController=AbortController;w.scrollTo=()=>{};
 const timeout=w.setTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>ms===3000?(retry=fn,0):ms===2500?0:timeout(fn,ms,...args);
 w.fetch=async(path,options={})=>{requests.push({path,method:options.method||'GET'});if(path===failedPath&&fail){fail=false;throw Error('Isolated startup interruption')}inflight++;try{return await fetch(base+path,options)}finally{inflight--}};
 try{
  w.eval(fs.readFileSync('ui.js','utf8'));
  for(let i=0;i<100&&!retry;i++)await sleep(25);
  assert.ok(retry,'startup interruption schedules retry');assert.ok(d.querySelector('#agent-name-0'));
  function input(selector,value,event='input'){const field=d.querySelector(selector);assert.ok(field,selector);field.value=value;field.dispatchEvent(new w.Event(event,{bubbles:true}))}
  input('#agent-name-0','Startup draft name');input('#slackChannel','C12345678');input('[data-connection-field="bot_token"]','not-a-real-token');
  input('#profileOS','macos','change');input('#profileLocation','remote','change');input('#profileBudget','25','change');
  input('#vmMemory','4096');input('#vmCpus','2');input('#vmIso','runtime/vm-downloads/draft.iso');
  await retry();
  assert.equal(d.querySelector('#agent-name-0').value,'Startup draft name','startup retry must preserve team draft');
  assert.equal(d.querySelector('#slackChannel').value,'C12345678');assert.equal(d.querySelector('[data-connection-field="bot_token"]').value,'not-a-real-token');
  assert.equal(d.querySelector('#profileOS').value,'macos');assert.equal(d.querySelector('#profileLocation').value,'remote');assert.equal(d.querySelector('#profileBudget').value,'25');
  assert.equal(d.querySelector('#vmMemory').value,'4096');assert.equal(d.querySelector('#vmCpus').value,'2');assert.equal(d.querySelector('#vmIso').value,'runtime/vm-downloads/draft.iso');
  assert.ok(d.querySelector('#teamSaveState').textContent.includes('저장하지'));assert.ok(d.querySelector('#vmSaveState').textContent.includes('저장하지'));
  const unload=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
  assert.equal(requests.filter(r=>r.path==='/api/setup').length,1,'retry retains original settings revision');
  assert.equal(requests.filter(r=>r.method==='POST'&&['/api/setup/team','/api/setup/slack','/api/setup/vm','/api/setup/execution-profile','/api/vm/control'].includes(r.path)).length,0);
  const savedTeam=await(await fetch(base+'/api/setup')).json(),savedVM=await(await fetch(base+'/api/vm')).json();
  d.querySelector('[data-action="discard-settings"]').click();
  for(let i=0;i<100&&requests.filter(r=>r.path==='/api/vm').length<(failedPath==='/api/vm'?3:2);i++)await sleep(20);
  for(let i=0;i<100&&(inflight||d.querySelector('#vmMemory').value!==String(savedVM.config.memory_mb));i++)await sleep(20);
  assert.equal(d.querySelector('#agent-name-0').value,savedTeam.team.agents[0].name);
  assert.equal(d.querySelector('[data-connection-field="bot_token"]').value,'');
  assert.equal(d.querySelector('#vmMemory').value,String(savedVM.config.memory_mb));
  assert.equal(d.querySelector('#teamSaveState').textContent,'저장됨');assert.equal(d.querySelector('#vmSaveState').textContent,'');
 }finally{for(let i=0;i<100&&inflight;i++)await sleep(20);await sleep(50);w.close()}
}
(async()=>{await scenario('/api/state');await scenario('/api/vm');console.log('PASS: startup state/VM interruption preserves team, Slack token, profile and VM drafts; explicit discard reloads; no save/control requests')})().catch(error=>{console.error(error);process.exitCode=1});

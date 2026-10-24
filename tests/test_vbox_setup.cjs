// DOM controls use a fake VirtualBox adapter; actual HTTP only validates bad paths.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'../runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const bad=await fetch(base+'/api/vbox/plan',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify({profile:{location:'shared_vm',target_os:'windows'},iso:'../outside.iso'})});assert.equal(bad.status,400);
 const realStatus=await(await fetch(base+'/api/vbox/setup')).json();assert.equal(realStatus.preparation,null);assert.equal(realStatus.restoration,null);
 const absent=await fetch(base+'/api/vbox/recovery',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify({action:'inspect'})});assert.equal(absent.status,200);assert.equal((await absent.json()).can_clear,false);
 const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:base+'/#settings/environment',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
 w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;
 let prepareCount=0,late=null,delay=false,record=null,recoveryAllowed=false,clearCount=0,storedPlan=null,planCount=0,restoration=null;
 w.fetch=(path,options)=>{
  if(path==='/api/vbox/plan'){planCount++;const request=JSON.parse(options.body);const result={request:{...request,preset:request.preset==='auto'?'balanced':request.preset},requested_preset:request.preset,iso_sha256:'0'.repeat(64),official_checksum_verified:false,iso_detection:{os_type_id:'Windows11_64',unattended_supported:true},id:'fake-plan',target_os:'windows',preset:{memory_mb:4096,cpus:2},disk_mb:131072,recommendation_note:'현재 여유 자원 확인 필요',issues:[],can_prepare:true,commands:[['createvm','<img src=x onerror=alert(1)>']],scope:'파일 준비만'};storedPlan=result;if(delay)return new Promise(resolve=>{late=()=>resolve(new Response(JSON.stringify(result)))});return Promise.resolve(new Response(JSON.stringify(result)))}
  if(path==='/api/vbox/prepare'){prepareCount++;assert.equal(JSON.parse(options.body).plan_id,'fake-plan');record={state:'prepared',completed_steps:6,scope:'부팅하지 않음'};return Promise.resolve(new Response(JSON.stringify(record)))}
  if(path==='/api/vbox/recovery'){const payload=JSON.parse(options.body);if(payload.action==='clear'){clearCount++;assert.equal(payload.record_id,'fake-plan');assert.ok(payload.request_key.length>=8);record=null;storedPlan=null;return Promise.resolve(new Response(JSON.stringify({cleared:true,record_id:'fake-plan'})))}return Promise.resolve(new Response(JSON.stringify({record_id:'fake-plan',name:'fake-vm',can_clear:recoveryAllowed,reason:recoveryAllowed?'VM 등록과 폴더가 없습니다.':'VM 등록이 남아 있습니다.'})))}
  if(path==='/api/vbox/setup')return Promise.resolve(new Response(JSON.stringify({preparation:record,plan:storedPlan,restoration})));
  return fetch(base+path,options);
 };
 const timers=new Set(),set=w.setTimeout.bind(w),clear=w.clearTimeout.bind(w);let closing=false;
 w.setTimeout=(fn,ms,...args)=>{if(closing)return 0;const id=set(()=>{timers.delete(id);fn(...args)},ms);timers.add(id);return id};w.clearTimeout=id=>{timers.delete(id);clear(id)};
 const input=(id,value,event='change')=>{const node=d.querySelector('#'+id);node.value=value;node.dispatchEvent(new w.Event(event,{bubbles:true}))};
 const click=action=>d.querySelector('[data-action="'+action+'"]').click();
 async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await sleep(40)}throw Error(label+' / '+d.querySelector('#toast').textContent)}
 w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,`const track=fn=>async(...args)=>{window.vboxPending++;try{return await fn(...args)}finally{window.vboxPending--}};window.vboxPending=0;busy=track(busy);poll=track(poll);boot=track(boot);boot();`));
 try{
  await until(()=>d.querySelectorAll('.agent-card').length>0,'boot');assert.equal(d.querySelector('#vboxOptions').open,false);
  input('profileLocation','shared_vm');input('profileOS','windows');input('vboxIso','runtime/install.iso','input');click('plan-vbox');
  await until(()=>!d.querySelector('[data-action="prepare-vbox"]').hidden,'reviewed plan shown');
  assert.ok(d.querySelector('#vboxSummary').textContent.includes('CPU 2개'));assert.equal(d.querySelector('#vboxCommands img'),null);
  assert.ok(d.querySelector('#vboxSummary').textContent.includes('Windows11_64'));assert.ok(d.querySelector('#vboxSummary').textContent.includes('공식 체크섬 미대조'));
  const count=planCount;input('vboxSha256','','input');assert.equal(d.querySelector('[data-action="prepare-vbox"]').hidden,true);click('status-vbox');await until(()=>!d.querySelector('[data-action="prepare-vbox"]').hidden,'completed preview recovered without rehash');assert.equal(planCount,count);
  input('vboxPreset','light');assert.equal(d.querySelector('[data-action="prepare-vbox"]').hidden,true);
  click('plan-vbox');await until(()=>!d.querySelector('[data-action="prepare-vbox"]').hidden,'new plan');click('prepare-vbox');
  await until(()=>d.querySelector('#vboxSummary').textContent.includes('준비 완료 기록'),'fake preparation receipt');assert.equal(prepareCount,1);assert.equal(d.querySelector('[data-action="prepare-vbox"]').hidden,true);
  record={state:'preparing',completed_steps:2};click('status-vbox');await until(()=>d.querySelector('#vboxSummary').textContent.includes('생존과 완료 여부는 미확인'),'no false live process claim');
  click('inspect-vbox-recovery');await until(()=>d.querySelector('#vboxRecoverySummary').textContent.includes('등록이 남아'),'remaining VM blocks cleanup');assert.equal(d.querySelector('[data-action="clear-vbox-recovery"]').hidden,true);
  recoveryAllowed=true;click('inspect-vbox-recovery');await until(()=>!d.querySelector('[data-action="clear-vbox-recovery"]').hidden,'absence permits metadata cleanup');click('clear-vbox-recovery');await until(()=>d.querySelector('#vboxRecoverySummary').textContent.includes('준비 기록을 정리'),'metadata clear receipt');assert.equal(clearCount,1);assert.equal(d.querySelector('#vboxIso').value,'runtime/install.iso');
  restoration={archived_records:2};click('status-vbox');await until(()=>d.querySelector('#vboxSummary').textContent.includes('별도 보관'),'restored records kept detached');
  assert.ok(d.querySelector('#vboxSummary').textContent.includes('2건'));assert.equal(d.querySelector('[data-action="prepare-vbox"]').hidden,true);assert.equal(d.querySelector('#vboxInstallOptions').hidden,true);restoration=null;
  delay=true;click('plan-vbox');await until(()=>late,'pending plan');input('vboxIso','runtime/new.iso','input');late();
  await until(()=>!d.querySelector('[data-action="plan-vbox"]').disabled,'late response drained');assert.equal(d.querySelector('[data-action="prepare-vbox"]').hidden,true);assert.equal(d.querySelector('#vboxIso').value,'runtime/new.iso');
  console.log('PASS: VirtualBox collapsed preparation review, safe command text, stale-plan invalidation, fake prepare receipt, unknown process status; no actual VirtualBox commands');
 }finally{closing=true;for(const id of timers)clear(id);timers.clear();await until(()=>w.vboxPending===0,'pending actions drained');dom.window.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1});

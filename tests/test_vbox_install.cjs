// Installation controls use fake native responses. No VirtualBox commands run.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'../runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const invalid=await fetch(base+'/api/vbox/install',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify({})});assert.equal(invalid.status,400);
 const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:base+'/#settings/environment',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
 w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;
 const plan={id:'fake-install-plan',target_os:'windows',iso_detection:{unattended_supported:true,images:[{index:1,name:'Home'},{index:2,name:'<b>Pro</b>'}]}},record={id:plan.id,state:'prepared',name:'fake VM'};
 let requests=[],release=null;
 w.fetch=(path,options)=>{if(path==='/api/vbox/install'){requests.push(JSON.parse(options.body));return new Promise((resolve,reject)=>{const id=requests.at(-1).plan_id;release=(failure=false)=>failure?reject(Error('fixture delayed error')):resolve(new Response(JSON.stringify({plan_id:id,state:'media_prepared',username:'moa',image_index:2})))})}return fetch(base+path,options)};
 const timers=new Set(),set=w.setTimeout.bind(w),clear=w.clearTimeout.bind(w);let closing=false;
 w.setTimeout=(fn,ms,...args)=>{if(closing)return 0;const id=set(()=>{timers.delete(id);fn(...args)},ms);timers.add(id);return id};w.clearTimeout=id=>{timers.delete(id);clear(id)};
 async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await sleep(40)}throw Error(label+' / '+d.querySelector('#toast').textContent)}
 const input=(id,value)=>{const node=d.querySelector('#'+id);node.value=value;node.dispatchEvent(new w.Event('input',{bubbles:true}))};
 w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,`const track=fn=>async(...args)=>{window.installPending++;try{return await fn(...args)}finally{window.installPending--}};window.installPending=0;busy=track(busy);poll=track(poll);boot=track(boot);window.showInstallation=renderVboxInstall;window.installDirty=hasVboxInstallDraft;window.invalidateInstallation=hideVboxPlan;boot();`));
 try{
  await until(()=>d.querySelectorAll('.agent-card').length>0,'boot');assert.equal(d.querySelector('#vboxInstallOptions').hidden,true);
  w.showInstallation(plan,record,null);assert.equal(d.querySelector('#vboxInstallOptions').hidden,false);assert.equal(d.querySelector('#vboxInstallOptions').open,false);assert.equal(d.querySelector('#vboxGuestImage').value,'');assert.equal(d.querySelector('#vboxGuestImage b'),null);
  d.querySelector('[data-action="install-vbox"]').click();await until(()=>d.querySelector('#toast').textContent.includes('판본'),'edition required');assert.equal(requests.length,0);
  input('vboxGuestImage','2');d.querySelector('[data-action="generate-vbox-password"]').click();const generated=d.querySelector('#vboxGuestPassword').value;assert.ok(/^Moa-[a-f0-9]{32}$/.test(generated));assert.equal(w.installDirty(),true);
  d.querySelector('[data-action="install-vbox"]').click();await until(()=>release,'fake pending installation');input('vboxGuestPassword','Moa-newDraftSecret42');release();await until(()=>!d.querySelector('[data-action="install-vbox"]').disabled||d.querySelector('#vboxInstallSummary').textContent.includes('완료 기록'),'receipt');
  assert.equal(d.querySelector('[data-action="install-vbox"]').disabled,true);assert.equal(requests[0].password,generated);assert.equal(requests[0].image_index,2);assert.ok(requests[0].request_key.length>=8);assert.equal(d.querySelector('#vboxGuestPassword').value,'Moa-newDraftSecret42');assert.equal(w.installDirty(),true);
  d.querySelector('[data-action="discard-vbox-install"]').click();assert.equal(d.querySelector('#vboxGuestPassword').value,'');assert.equal(w.installDirty(),false);
  assert.ok(d.querySelector('#vboxInstallSummary').textContent.includes('OS 설치·부팅은 미검증'));
  await until(()=>w.installPending===0,'previous discard settled');
  input('vboxGuestPassword','Moa-keepOriginalDraft42');
  const nextPlan={...plan,id:'different-install-plan'};
  w.showInstallation(nextPlan,{...record,id:nextPlan.id,name:'different VM'},null);
  assert.equal(d.querySelector('#vboxGuestPassword').value,'Moa-keepOriginalDraft42','changing plan must preserve draft');
  assert.equal(d.querySelector('[data-action="install-vbox"]').disabled,true,'old guest credentials must not transfer to another VM');
  d.querySelector('[data-action="discard-vbox-install"]').click();
  await until(()=>w.installPending===0,'changed-plan discard settled');
  assert.equal(d.querySelector('#vboxGuestPassword').value,'');
  assert.equal(d.querySelector('[data-action="install-vbox"]').disabled,false);
  input('vboxGuestImage','2');input('vboxGuestPassword','Moa-pendingNextVM42');release=null;
  d.querySelector('[data-action="install-vbox"]').click();await until(()=>release,'second VM request pending');
  const thirdPlan={...plan,id:'third-install-plan'};
  w.showInstallation(thirdPlan,{...record,id:thirdPlan.id,name:'third VM'},null);
  const changedSummary=d.querySelector('#vboxInstallSummary').textContent;
  release();await until(()=>w.installPending===0,'old success settled');
  assert.equal(d.querySelector('#vboxInstallSummary').textContent,changedSummary,'old success must not overwrite current plan');
  assert.equal(d.querySelector('#vboxGuestPassword').value,'Moa-pendingNextVM42');
  assert.equal(d.querySelector('[data-action="install-vbox"]').disabled,true);
  d.querySelector('[data-action="discard-vbox-install"]').click();await until(()=>w.installPending===0,'third VM discard settled');
  input('vboxGuestImage','2');input('vboxGuestPassword','Moa-pendingFailure42');release=null;
  d.querySelector('[data-action="install-vbox"]').click();await until(()=>release,'failure request pending');
  w.showInstallation(null,null,null);w.showInstallation(thirdPlan,{...record,id:thirdPlan.id},null);
  const latestSummary=d.querySelector('#vboxInstallSummary').textContent;
  release(true);await until(()=>w.installPending===0,'old error settled');
  assert.equal(d.querySelector('#vboxInstallSummary').textContent,latestSummary,'old failure must not overwrite a newer status for the same target');
  assert.equal(d.querySelector('#vboxGuestPassword').value,'Moa-pendingFailure42');
  w.invalidateInstallation();
  d.querySelector('[data-action="discard-vbox-install"]').click();await until(()=>w.installPending===0,'invalidated draft discard settled');
  assert.equal(d.querySelector('[data-action="install-vbox"]').disabled,true,'discard must not reactivate invalidated preparation');
  console.log('PASS: owned VM install form, explicit edition, safe labels, generated guest password, delayed receipt draft preservation, cross-VM draft guard, stale success/error isolation; fake installation only');
 }finally{closing=true;for(const id of timers)clear(id);timers.clear();await until(()=>w.installPending===0,'pending actions drained');dom.window.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1});

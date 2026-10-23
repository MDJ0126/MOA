// Installation controls use fake native responses. No VirtualBox commands run.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'./runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const invalid=await fetch(base+'/api/vbox/install',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Agent':'1'},body:JSON.stringify({})});assert.equal(invalid.status,400);
 const dom=new JSDOM(fs.readFileSync('dashboard.html','utf8'),{url:base+'/#settings/environment',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
 w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;
 const plan={id:'fake-install-plan',target_os:'windows',iso_detection:{unattended_supported:true,images:[{index:1,name:'Home'},{index:2,name:'<b>Pro</b>'}]}},record={id:plan.id,state:'prepared',name:'fake VM'};
 let requests=[],release=null;
 w.fetch=(path,options)=>{if(path==='/api/vbox/install'){requests.push(JSON.parse(options.body));return new Promise(resolve=>{release=()=>resolve(new Response(JSON.stringify({plan_id:plan.id,state:'media_prepared',username:'moa',image_index:2})))})}return fetch(base+path,options)};
 const timers=new Set(),set=w.setTimeout.bind(w),clear=w.clearTimeout.bind(w);let closing=false;
 w.setTimeout=(fn,ms,...args)=>{if(closing)return 0;const id=set(()=>{timers.delete(id);fn(...args)},ms);timers.add(id);return id};w.clearTimeout=id=>{timers.delete(id);clear(id)};
 async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await sleep(40)}throw Error(label+' / '+d.querySelector('#toast').textContent)}
 const input=(id,value)=>{const node=d.querySelector('#'+id);node.value=value;node.dispatchEvent(new w.Event('input',{bubbles:true}))};
 w.eval(fs.readFileSync('ui.js','utf8').replace(/boot\(\);\s*$/,`const track=fn=>async(...args)=>{window.installPending++;try{return await fn(...args)}finally{window.installPending--}};window.installPending=0;busy=track(busy);poll=track(poll);boot=track(boot);window.showInstallation=renderVboxInstall;window.installDirty=hasVboxInstallDraft;boot();`));
 try{
  await until(()=>d.querySelectorAll('.agent-card').length>0,'boot');assert.equal(d.querySelector('#vboxInstallOptions').hidden,true);
  w.showInstallation(plan,record,null);assert.equal(d.querySelector('#vboxInstallOptions').hidden,false);assert.equal(d.querySelector('#vboxInstallOptions').open,false);assert.equal(d.querySelector('#vboxGuestImage').value,'');assert.equal(d.querySelector('#vboxGuestImage b'),null);
  d.querySelector('[data-action="install-vbox"]').click();await until(()=>d.querySelector('#toast').textContent.includes('판본'),'edition required');assert.equal(requests.length,0);
  input('vboxGuestImage','2');d.querySelector('[data-action="generate-vbox-password"]').click();const generated=d.querySelector('#vboxGuestPassword').value;assert.ok(/^Moa-[a-f0-9]{32}$/.test(generated));assert.equal(w.installDirty(),true);
  d.querySelector('[data-action="install-vbox"]').click();await until(()=>release,'fake pending installation');input('vboxGuestPassword','Moa-newDraftSecret42');release();await until(()=>!d.querySelector('[data-action="install-vbox"]').disabled||d.querySelector('#vboxInstallSummary').textContent.includes('완료 기록'),'receipt');
  assert.equal(d.querySelector('[data-action="install-vbox"]').disabled,true);assert.equal(requests[0].password,generated);assert.equal(requests[0].image_index,2);assert.ok(requests[0].request_key.length>=8);assert.equal(d.querySelector('#vboxGuestPassword').value,'Moa-newDraftSecret42');assert.equal(w.installDirty(),true);
  d.querySelector('[data-action="discard-vbox-install"]').click();assert.equal(d.querySelector('#vboxGuestPassword').value,'');assert.equal(w.installDirty(),false);
  assert.ok(d.querySelector('#vboxInstallSummary').textContent.includes('OS 설치·부팅은 미검증'));
  console.log('PASS: owned VM install form, explicit edition, safe labels, generated guest password, delayed receipt draft preservation; fake installation only');
 }finally{closing=true;for(const id of timers)clear(id);timers.clear();await until(()=>w.installPending===0,'pending actions drained');dom.window.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1});

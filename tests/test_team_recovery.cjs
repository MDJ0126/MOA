const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.MOA_JSDOM||'../runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:base+'/#settings/team',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,requests=[];let sent=null;
w.structuredClone=structuredClone;w.AbortController=AbortController;w.scrollTo=()=>{};
w.fetch=async(path,options={})=>{
 const body=options.body?JSON.parse(options.body):null;requests.push({path,body});
 if(path==='/api/setup'){const actual=await(await fetch(base+path)).json();actual.team={...actual.team,configured:false,recovery_required:true,recovery_token:'a'.repeat(64),recovery_message:'저장된 팀 설정을 읽지 못했습니다. 구성을 확인하고 저장하면 원문을 보관하고 복구합니다.'};return new Response(JSON.stringify(actual))}
 if(path==='/api/setup/team'){sent=body;const saved={...body,configured:true,updated:999};delete saved.recovery_token;delete saved.expected_updated;return new Response(JSON.stringify(saved))}
 return fetch(base+path,options);
};
async function until(fn){for(let i=0;i<150;i++){if(fn())return;await sleep(25)}throw Error('Team recovery DOM timeout: '+d.querySelector('#toast').textContent)}
(async()=>{try{
 w.eval(fs.readFileSync('src/web/ui.js','utf8'));await until(()=>d.querySelector('#teamSaveState').textContent.includes('원문을 보관')&&!d.querySelector('#onboarding').hidden);
 assert.equal(sent,null,'opening recovery view must not save');assert.equal(d.querySelector('#onboarding').hidden,false);
 d.querySelector('[data-preset="light"]').click();d.querySelector('#saveTeam').click();
 await until(()=>d.querySelector('#teamSaveState').textContent==='저장됨'&&d.querySelector('#onboarding').hidden);
 assert.equal(sent.recovery_token,'a'.repeat(64));assert.equal(sent.expected_updated>=0,true);assert.equal(sent.agents[0].id,'builder');
 assert.equal(d.querySelector('#agent-name-0').value,'지우');assert.equal(d.querySelector('#onboarding').hidden,true);
 assert.equal(requests.filter(r=>r.body&&['/api/vm/control','/api/slack/test','/api/setup/slack'].includes(r.path)).length,0);
 console.log('PASS: corrupt team recovery notice, explicit preset save carries original fingerprint, recovery clears; no VM/Slack controls');
 }finally{await sleep(100);w.close()}})().catch(error=>{console.error(error);process.exitCode=1});

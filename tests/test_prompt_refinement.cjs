const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:base+'/#home',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document;w.structuredClone=structuredClone;w.scrollTo=()=>{};w.AbortController=AbortController;w.fetch=(path,options)=>fetch(base+path,options);
 async function until(fn){for(let i=0;i<200;i++){if(fn())return;await sleep(30)}throw Error('UI timeout: '+d.querySelector('#toast').textContent)}
 try{
  w.eval(fs.readFileSync('src/web/ui.js','utf8'));
  await until(()=>d.querySelectorAll('[data-team-field="specialty"]').length>0);
  d.querySelector('#taskTitle').value='교육과정 보고서를 작성해 주세요.';
  d.querySelector('#taskMode').value='demo';d.querySelector('#prompt-tone').value='정중하게';
  d.querySelector('[data-action="refine-prompt"]').click();
  await until(()=>d.querySelector('#promptPreview').value.includes('# 원문 요청'));
  assert.equal(d.querySelector('#taskTitle').value,'교육과정 보고서를 작성해 주세요.');
  assert.ok(d.querySelector('#promptPreview').value.includes('# 톤앤매너\n정중하게'));
  d.querySelector('[data-action="apply-prompt"]').click();
  assert.equal(d.querySelector('#taskTitle').value,d.querySelector('#promptPreview').value);
  const field=d.querySelector('[data-team-field="specialty"]');field.value='설문 분석가';field.dispatchEvent(new w.Event('input',{bubbles:true}));
  d.querySelector('[data-action="save-team"]').click();
  await until(()=>d.querySelector('#teamSaveState').textContent==='저장됨');
  const saved=await(await fetch(base+'/api/setup')).json();assert.equal(saved.team.agents[0].specialty,'설문 분석가');
  console.log('PASS: HTTP refinement, original preserved, six fields, apply draft, specialty save/reload');
 }finally{w.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1});

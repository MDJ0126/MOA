const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('./runtime/ui-tests/node_modules/jsdom');
const base=process.argv[2],dom=new JSDOM(fs.readFileSync('dashboard.html','utf8'),{url:base+'/#work',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document;w.structuredClone=structuredClone;w.AbortController=AbortController;w.scrollTo=()=>{};w.fetch=(path,options)=>fetch(base+path,options);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn){for(let i=0;i<150;i++){if(fn())return;await sleep(50)}throw Error('DOM history timeout: '+d.querySelector('#toast').textContent)}
w.eval(fs.readFileSync('ui.js','utf8'));
async function main(){
 await until(()=>d.querySelectorAll('#allTasks [data-task]').length===30);
 d.querySelector('[data-action="next-page"]').click();await until(()=>d.querySelector('#workPage').textContent==='2 / 22');
 const search=d.querySelector('#taskSearch');search.value='History 000';search.dispatchEvent(new w.Event('input',{bubbles:true}));await until(()=>d.querySelector('#workCount').textContent==='1개 작업');
 d.querySelector('#allTasks [data-task]').click();await until(()=>d.querySelectorAll('#modalLogs .bubble-row').length===200);
 d.querySelector('#olderLogs').click();await until(()=>d.querySelectorAll('#modalLogs .bubble-row').length===400);
 d.querySelector('#olderLogs').click();await until(()=>d.querySelectorAll('#modalLogs .bubble-row').length===405);
 assert.equal(d.querySelector('#olderLogs').hidden,true);
 assert.ok(d.querySelector('#modalTitle').textContent==='History 000');
 console.log('PASS: DOM pagination, old task outside snapshot, 405 older conversations loaded with terminal cursor');
}
main().then(()=>dom.window.close()).catch(error=>{console.error(error);dom.window.close();process.exitCode=1});

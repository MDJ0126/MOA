const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('../runtime/ui-tests/node_modules/jsdom');
const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:'http://127.0.0.1/#home',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;w.AbortController=AbortController;w.structuredClone=structuredClone;
w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,'')+'\nwindow.statusBarTest=s=>{state=s;renderGlobalWorkStatus()};');
const agents=[{id:'chief',name:'김하준',rank:'팀장',capabilities:['planner']},{id:'dev',name:'이지우',rank:'과장',job_title:'개발',capabilities:['developer']}],plan={agents,pipeline:['planner','developer'],assignment:{planner:'chief',developer:'dev'}};
const base={tasks:[],leader_requests:[],paused:false};w.statusBarTest(base);assert.equal(d.querySelector('#globalWorkStatus').hidden,true);
w.statusBarTest({...base,leader_requests:[{id:'receipt',state:'planning',leader_name:'김하준 팀장 (총괄)'}]});assert.match(d.querySelector('#globalWorkStatus').textContent,/김하준.*요청 정리·배정 중/);
const task={id:'job',status:'running',stage:1,title:'실제 상태 테스트',execution_plan:JSON.stringify(plan)};
for(const page of ['home','resources','settings/team','work']){w.location.hash=page;w.statusBarTest({...base,tasks:[task]});assert.equal(d.querySelector('#globalWorkStatus').hidden,false);assert.match(d.querySelector('#globalWorkStatus').textContent,/이지우 과장.*개발.*진행 중/);assert.equal(d.querySelector('#globalWorkStatus [data-task]').dataset.task,'job');assert.ok(d.body.classList.contains('has-work-status'))}
w.statusBarTest({...base,tasks:[{...task,execution_plan:JSON.stringify({...plan,stage_labels:{developer:'자료 조사'}})}],messages:[{task_id:'job',kind:'progress',content:'출처 문서를 열람하고 있습니다.'}]});assert.match(d.querySelector('#globalWorkStatus').textContent,/자료 조사.*출처 문서/);
w.statusBarTest({...base,tasks:[task],paused:true});assert.match(d.querySelector('#globalWorkStatus').textContent,/일시 중지/);
w.statusBarTest({...base,tasks:[{...task,status:'queued',wait_reason:'slack_delivery'}]});assert.match(d.querySelector('#globalWorkStatus').textContent,/Slack 전달 대기/);
w.statusBarTest({...base,tasks:[{...task,status:'completed'}]});assert.equal(d.querySelector('#globalWorkStatus').hidden,true);assert.equal(d.body.classList.contains('has-work-status'),false);
console.log('PASS: global footer shows assigned worker, leader processing, pause/queue, all routes and hides after completion');dom.window.close();

const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../runtime/ui-tests/node_modules/jsdom');
const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:'http://localhost/#goals',runScripts:'outside-only'}),w=dom.window,d=w.document;
w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,'')+'\nwindow.goalTest=value=>{state=value;renderGoals()};');
try{
 const data={continuous_goals:{paused:false,scheduler:{alive:true,last_loop_at:1,error:''},goals:[{id:'g',title:'<보안> 목표',state:'active',deadline:Date.now()/1000+3600,max_tasks:20,items:[{id:'i',ordinal:1,title:'검증한 문서',task_id:'abcdef123456',state:'ready',review:'pending',task:{timing:{elapsed_seconds:12},quality_review:{review_count:2,rejection_count:1}}}]}]}};
 w.goalTest(data);assert.ok(d.querySelector('[data-page="goals"]'));assert.match(d.querySelector('#goalList').textContent,/<보안> 목표/);assert.equal(d.querySelector('#goalList 보안'),null);
 assert.match(d.querySelector('#goalList').textContent,/검토 2회 · 반려 1회/);assert.ok(d.querySelector('[data-task="abcdef123456"]'));assert.ok(d.querySelector('[data-goal-review="approved"]'));
 const input=d.querySelector('[data-goal-feedback]');input.value='부분 수정 요청';input.focus();input.setSelectionRange(2,4);data.continuous_goals.goals[0].reason='상태 변경';w.goalTest(data);
 const restored=d.querySelector('[data-goal-feedback]');assert.equal(restored.value,'부분 수정 요청');assert.equal(restored.selectionStart,2);assert.equal(restored.selectionEnd,4);
 data.continuous_goals.scheduler.last_loop_at=2;data.continuous_goals.goals[0].items[0].task.timing.elapsed_seconds=15;w.goalTest(data);assert.equal(d.querySelector('[data-goal-feedback]'),restored);assert.match(d.querySelector('[data-goal-duration]').textContent,/15초/);
 data.continuous_goals.paused=true;w.goalTest(data);assert.match(d.querySelector('#goalGlobalStatus').textContent,/전체 업무가 일시 중지/);
 console.log('PASS: goal review queue, escaped titles, evidence links, metrics and draft/caret preservation');
}finally{w.close()}

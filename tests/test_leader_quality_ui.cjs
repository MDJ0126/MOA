const fs=require('fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../runtime/ui-tests/node_modules/jsdom');
const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:'http://localhost/#home',runScripts:'outside-only'}),w=dom.window,d=w.document;
w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,'')+'\nwindow.qualityUI=(value,task)=>{state=value;renderComposerRecentDuration();renderLeaderReviews(task)};');
try{
 const task={id:'qa',title:'부분 수정 검증',status:'completed',revision:1,execution_plan:JSON.stringify({agents:[{id:'chief',name:'리더',capabilities:['planner']}],pipeline:['planner'],assignment:{planner:'chief'},leader_review:{enabled:true}}),timing:{recorded:true,estimated:false,elapsed_seconds:90,finished_at:100},quality_review:{review_count:2,rejection_count:1,reviewer_checks:2,recheck_count:0,approved:true,rounds:[{round:1,decision:'revise',reason:'누락된 요구사항',instructions:'해당 부분만 수정',checks:[{criterion:'결과물',evidence:'answer.txt',passed:false}]},{round:2,decision:'approve',reason:'수정 결과 확인',instructions:'',checks:[{criterion:'검증',evidence:'실행 통과',passed:true}]}]}};
 w.qualityUI({tasks:[{...task,id:'older',timing:{...task.timing,finished_at:50,elapsed_seconds:10}},task]},task);
 assert.equal(d.querySelector('#composerRecentDuration').textContent,'최근 1분 30초');assert.equal(d.querySelector('#composerRecentDuration').hidden,false);
 const panel=d.querySelector('#leaderReviewPanel');assert.match(panel.querySelector('summary').textContent,/검토 2회 · 반려 1회 · 수정 1회 · 최종 승인/);assert.match(panel.textContent,/해당 부분만 수정/);assert.equal(panel.querySelectorAll('article').length,2);
 task.quality_review.rounds[0].instructions='<img src=x onerror=alert(1)>';w.qualityUI({tasks:[]},task);assert.equal(panel.querySelector('img'),null);assert.equal(d.querySelector('#composerRecentDuration').hidden,true);
 console.log('PASS: latest completed duration, persisted review counters/reasons/partial instructions and safe rendering');
}finally{w.close()}

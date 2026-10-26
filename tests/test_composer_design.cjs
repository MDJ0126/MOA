const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../runtime/ui-tests/node_modules/jsdom');
const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:'http://localhost/#home',runScripts:'outside-only'}),w=dom.window,d=w.document;
w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,'')+'\nwindow.renderComposerTest=value=>{state=value;lastLeaderReceipt=null;renderComposerIdentity()};');
try{
 const base={tasks:[],team:{agents:[{id:'staff',name:'직원',manager_id:'chief'},{id:'chief',name:'김하준',rank:'팀장',manager_id:''}]},agent_profiles:{chief:{url:'/profile-chief.png'}},leader_requests:[]};
 d.querySelector('#taskTitle').value='작성 중인 요청';w.renderComposerTest(base);
 assert.match(d.querySelector('#composerLeader').textContent,/김하준 팀장/);assert.equal(d.querySelector('#composerLeader img').getAttribute('src'),'/profile-chief.png');assert.equal(d.querySelector('#composerStatus').dataset.state,'ready');
 base.leader_requests=[{id:'request',state:'planning',created:Date.now()/1000-48}];w.renderComposerTest(base);assert.match(d.querySelector('#composerStatus').textContent,/요청 정리·배정 중 · 48초/);
 base.paused=true;w.renderComposerTest(base);assert.equal(d.querySelector('#composerStatus').textContent,'일시 중지');
 base.paused=false;base.leader_requests=[];base.tasks=[{id:'task',status:'running',title:'실제 작업',created:Date.now()/1000-120}];w.renderComposerTest(base);assert.match(d.querySelector('#composerStatus').textContent,/업무 진행 중 · 2분/);
 base.tasks[0].status='completed';w.renderComposerTest(base);assert.equal(d.querySelector('#composerStatus').dataset.state,'ready');assert.equal(d.querySelector('#taskTitle').value,'작성 중인 요청');
 const trigger=d.querySelector('#composerLeader'),menu=d.querySelector('#composerRecipients');trigger.click();assert.equal(menu.hidden,false);assert.equal(trigger.getAttribute('aria-expanded'),'true');d.querySelector('[data-composer-recipient="staff"]').click();assert.equal(menu.hidden,true);assert.match(trigger.textContent,/직원/);assert.match(d.querySelector('#taskTitle').placeholder,/직원/);assert.equal(d.querySelector('#taskTitle').value,'작성 중인 요청');trigger.click();trigger.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(menu.hidden,true);
 assert.ok(d.querySelector('.composer-toolbar #composerExtras'));assert.ok(d.querySelector('#submitTask svg'));console.log('PASS: live leader profile, request/task elapsed time, pause, completion and draft preservation');
}finally{w.close()}

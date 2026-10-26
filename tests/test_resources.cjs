// Resource dashboard DOM tests: install/Slack/VM effects are mocked and labeled.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../runtime/ui-tests/node_modules/jsdom');
const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:'http://127.0.0.1/#resources',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document;w.structuredClone=structuredClone;w.AbortController=AbortController;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
const team={updated:1,pipeline:['developer','reviewer'],max_revisions:2,agents:[{id:'a',name:'지우',capabilities:['developer'],instructions:'기존 개발 지침'},{id:'b',name:'서연',capabilities:['reviewer'],instructions:'기존 검증 지침'}]};
const model={checked_at:Date.now()/1000,winget:true,guest:{},guest_session:'s',guest_network:false,installed:[{name:'Git',version:'2.4',publisher:'Git'}],programs:[{id:'git',name:'Git',icon:'git',site:'https://git-scm.com/',category:'개발',description:'버전 관리',installed:false,version:'',evidence:'없음',match:['git'],guest:'git'},{id:'python',name:'Python',icon:'python',site:'https://python.org/',category:'개발',description:'코드 작성',installed:true,version:'3.12',evidence:'registry',match:['python'],guest:'python3.6'}],skills:[{id:'qa',name:'실행 QA',icon:'quality',description:'실제 실행 확인',instructions:'검증하세요',installed:false,agents:[],builtin:true}],jobs:[],policy:{auto_programs:false,auto_skills:true,allowed_programs:[],package_terms:true,source_terms:true,global_instructions:'기존 전역 지침',updated:1}};
const calls=[];
w.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):{};calls.push({path,body});let result;
 if(path==='/api/resources/snapshot')result=structuredClone(model);
 else if(path==='/api/resources/policy'){model.policy={...body,updated:model.policy.updated+1};result=model.policy}
 else if(path==='/api/resources/skill'){const value={...body,id:body.id||'custom-test'};const i=model.skills.findIndex(s=>s.id===value.id);if(i<0)model.skills.push(value);else model.skills[i]=value;result=value}
 else if(path==='/api/resources/install'){model.jobs.push({id:'job',program:body.programs[0],target:body.target,actor:'user',state:'completed',message:'fixture install confirmation'});result={jobs:model.jobs}}
 else if(path==='/api/resources/recommend')result={target:'guest',basis:'키워드 추천',programs:['git'],skills:['qa']};
 else if(path==='/api/setup/team'){Object.assign(team,body,{updated:team.updated+1});result=team}
 else throw Error('Unexpected '+path);
 return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}})
};
w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,'')+'\nwindow.resourcesTest={init(value){setup={team:structuredClone(value),slack:{enabled:true,collaboration:true}};teamDraft=structuredClone(value);state.team=value;refresh=async()=>{};renderTeam=()=>{}},load:loadResources};');
w.resourcesTest.init(team);
const click=s=>{assert.ok(d.querySelector(s),s);d.querySelector(s).click()},input=(s,v)=>{const e=d.querySelector(s);e.value=v;e.dispatchEvent(new w.Event('input',{bubbles:true}))};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));async function wait(fn,label){for(let i=0;i<100;i++){if(fn())return;await sleep(10)}throw Error(label+' | '+d.querySelector('#toast').textContent)}
async function main(){
 await w.resourcesTest.load();assert.equal(d.querySelectorAll('.resource-card').length,2);assert.equal(d.querySelectorAll('.resource-card>.resource-icon').length,2);assert.match(d.querySelector('#resourceGuest').textContent,/VM ネットワーク|네트워크 꺼짐/);
 input('#resourceSearch','버전');assert.equal(d.querySelectorAll('.resource-card').length,1);input('#resourceSearch','');
 const check=d.querySelector('[data-resource-program="git"]');check.checked=true;check.dispatchEvent(new w.Event('change',{bubbles:true}));click('[data-resource-action="install-selected"]');await wait(()=>model.jobs.length===1,'install request');assert.deepEqual(calls.find(c=>c.path==='/api/resources/install').body,{programs:['git'],target:'host'});
 await wait(()=>d.querySelector('#resourceJobs').textContent.includes('fixture'),'job result');
 click('[data-resource-tab="skills"]');click('[data-resource-action="toggle-skill"]');await wait(()=>model.skills[0].installed,'skill add');await wait(()=>d.querySelector('[data-resource-action="new-skill"]'),'skills render');
 click('[data-resource-action="new-skill"]');input('#skillName','내 스킬');input('#skillDescription','짧은 설명');input('#skillInstructions','한국어로 검증 결과를 보고하세요.');click('[data-resource-action="save-skill"]');await wait(()=>model.skills.length===2,'custom save');await wait(()=>d.querySelectorAll('.resource-card').length===2,'custom visible');assert.match(d.querySelector('#skillSaveStatus').textContent,/저장/);
 click('[data-resource-tab="instructions"]');assert.equal(d.querySelector('#resourceGlobal').value,'기존 전역 지침');input('#resourceGlobal','전역 새 지침');input('[data-resource-instructions="a"]','봇별 새 지침');
 click('[data-resource-tab="programs"]');click('[data-resource-tab="instructions"]');assert.equal(d.querySelector('#resourceGlobal').value,'전역 새 지침');assert.equal(d.querySelector('[data-resource-instructions="a"]').value,'봇별 새 지침');
 click('[data-resource-action="save-global"]');await wait(()=>model.policy.global_instructions==='전역 새 지침','global saved');click('[data-resource-action="save-agent-instructions"]');await wait(()=>team.agents[0].instructions==='봇별 새 지침','agent saved');assert.equal(team.agents[1].instructions,'기존 검증 지침');
 input('#taskTitle','Git으로 QA');click('[data-resource-action="preflight"]');await wait(()=>!d.querySelector('#taskPreparation').hidden,'preflight');assert.match(d.querySelector('#taskPreparation').textContent,/VM 내부 확인 필요/);
 model.execution={selected:{location:'shared_vm',os:'linux'}};model.programs.push({id:'unity',name:'Unity',icon:'unity',site:'https://unity.com/',category:'게임',description:'게임 제작',installed:true,manual:true,match:['unity'],environment_support:{supported:false,label:'설치 미지원',reason:'Linux 호환 패키지 없음'}});
 click('[data-resource-tab="programs"]');await w.resourcesTest.load();
 const unity=[...d.querySelectorAll('.resource-card')].find(el=>el.querySelector('h3').textContent==='Unity');assert.match(unity.textContent,/설치 미지원/);assert.equal(unity.querySelector('button').disabled,true);assert.match(unity.textContent,/설치 미확인/);
 const python=[...d.querySelectorAll('.resource-card')].find(el=>el.querySelector('h3').textContent==='Python');assert.match(python.textContent,/선택 VM 설치 상태 미확인/);assert.equal(python.classList.contains('is-installed'),false);
 model.execution.selected.os='windows';model.programs[2].environment_support={supported:true,automatic:false,label:'직접 인증·설치 필요',reason:'엔진 로그인 필요'};await w.resourcesTest.load();assert.match(d.querySelector('#resourcePrograms').textContent,/직접 인증·설치 필요/);
 console.log('PASS: catalog/icons before installation, filter, selected host install, job receipt, built-in/custom skills, draft retention, global/agent instructions, VM-aware preflight. Effects mocked.');
}
main().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>dom.window.close());

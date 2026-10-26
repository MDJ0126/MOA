const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('../runtime/ui-tests/node_modules/jsdom');
const dom=new JSDOM(fs.readFileSync('src/web/dashboard.html','utf8'),{url:'http://localhost/#home',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,d=w.document;
w.eval(fs.readFileSync('src/web/ui.js','utf8').replace(/boot\(\);\s*$/,''));
const input=d.querySelector('#taskTitle'),backdrop=d.querySelector('#composerFocusBackdrop'),background=d.createElement('button');d.body.append(background);let clicks=0;background.addEventListener('click',()=>clicks++);
try{
 input.value='작성 중인 내용';input.focus();const down=new w.Event('pointerdown',{bubbles:true,cancelable:true});backdrop.dispatchEvent(down);assert.ok(down.defaultPrevented);assert.equal(d.activeElement,input);
 backdrop.click();assert.equal(clicks,0);assert.notEqual(d.activeElement,input);assert.equal(input.value,'작성 중인 내용');background.click();assert.equal(clicks,1);
 input.focus();const inside=d.querySelector('#composerLeader');inside.click();assert.equal(inside.getAttribute('aria-expanded'),'true');
 console.log('PASS: backdrop owns dismissal, pointerdown preserves focus until click, draft preserved, background and internal controls work afterward');
}finally{w.close()}

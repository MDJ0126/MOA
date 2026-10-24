// Export app guide fragments as readable GitHub Markdown. Run from repository root.
const fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require(process.env.MOA_JSDOM||'../runtime/ui-tests/node_modules/jsdom');
const root=path.resolve(__dirname,'..'),docs=path.join(root,'docs');
const guides=JSON.parse(fs.readFileSync(path.join(docs,'index.json'),'utf8'));
const names=new Set(guides.map(g=>g.id));
function children(n){return [...n.childNodes].map(render).join('')}
function render(n){
 if(n.nodeType===3)return n.textContent.replace(/\s+/g,' ');
 if(n.nodeType!==1)return '';
 const t=n.tagName.toLowerCase(),content=()=>children(n).trim();
 if(t==='pre')return '\n\n```\n'+n.textContent.trim()+'\n```\n\n';
 if(t==='code')return '`'+n.textContent.trim()+'`';
 if(/^h[1-6]$/.test(t))return '\n\n'+'#'.repeat(Number(t[1]))+' '+content()+'\n\n';
 if(t==='p'||t==='figure'||t==='div'||t==='details')return '\n\n'+content()+'\n\n';
 if(t==='summary')return '\n\n### '+content()+'\n\n';
 if(t==='strong'||t==='b')return '**'+content()+'**';
 if(t==='em'||t==='i')return '*'+content()+'*';
 if(t==='br')return '  \n';
 if(t==='ul'||t==='ol')return '\n\n'+[...n.children].map((li,i)=>(t==='ol'?(i+1)+'. ':'- ')+children(li).trim().replace(/\n+/g,' ')).join('\n')+'\n\n';
 if(t==='table'){
  const rows=[...n.querySelectorAll('tr')].map(tr=>[...tr.children].map(td=>children(td).trim().replace(/\|/g,'\\|').replace(/\n+/g,' ')));
  if(!rows.length)return '';
  return '\n\n| '+rows[0].join(' | ')+' |\n| '+rows[0].map(()=>'---').join(' | ')+' |\n'+rows.slice(1).map(row=>'| '+row.join(' | ')+' |').join('\n')+'\n\n';
 }
 if(t==='img'){
  const src=n.getAttribute('src')||'';
  return src.startsWith('/guide-images/')?'!['+(n.getAttribute('alt')||'안내 화면')+'](../screenshots/'+src.split('/').pop()+')':'';
 }
 if(t==='a'){
  let href=n.getAttribute('href')||'',label=content();
  if(/^https?:\/\/(127\.0\.0\.1|localhost)/.test(href))return label+' (`'+href+'`, 앱 실행 후 접속)';
  if(href.startsWith('/guide-images/'))href='../screenshots/'+href.split('/').pop();
  else if(href.startsWith('#docs/')){const id=href.slice(6);if(names.has(id))href=id+'.md';else return label;}
  else if(href==='#docs')href='../README.md';
  else if(href.startsWith('#')||href.startsWith('/'))return label+' (앱 메뉴)';
  return href?'['+label+']('+href+')':label;
 }
 if(t==='figcaption')return '\n\n*'+content()+'*\n\n';
 return children(n);
}
fs.mkdirSync(path.join(docs,'guides'),{recursive:true});
for(const guide of guides){
 const dom=new JSDOM(fs.readFileSync(path.join(docs,guide.id+'.html'),'utf8'));
 const label=dom.window.document.querySelector('.doc-label');if(label)label.remove();
 const body=children(dom.window.document.body).replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();
 const nav='[문서 모음](../README.md) · [프로젝트 소개](../../README.md)\n\n';
 fs.writeFileSync(path.join(docs,'guides',guide.id+'.md'),'<!-- Generated from docs/'+guide.id+'.html by tools/export_guides.cjs -->\n\n'+nav+body+'\n');
}
console.log('Exported '+guides.length+' readable guides.');

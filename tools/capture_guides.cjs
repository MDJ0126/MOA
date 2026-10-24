// Capture actual local UI with sample data only. No external services or VM input.
const {chromium}=require(process.env.MOA_PLAYWRIGHT||'../runtime/ui-tests/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path');
async function main(){
 const base=process.argv[2],output=process.argv[3];if(!base||!output)throw Error('base URL and output folder required');
 const browser=await chromium.launch({executablePath:process.env.MOA_BROWSER,headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1040},deviceScaleFactor:1,locale:'ko-KR'});
 await page.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin?route.continue():route.abort());
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 async function shot(name,selector){await page.locator(selector).screenshot({path:path.join(output,name+'.png'),animations:'disabled'})}
 try{
  await page.goto(base+'/#home');await page.locator('.agent-card').first().waitFor();await page.locator('#taskProfile').filter({hasText:'데모'}).waitFor();
  await shot('home','#page-home');
  await page.goto(base+'/#settings/team');await page.locator('#presetCards .preset').first().waitFor();await shot('team','#panel-team');
  await page.goto(base+'/#work');await page.locator('#allTasks [data-task]').first().waitFor();await shot('work','#page-work');
  await page.screenshot({path:path.join(output,'operations.png'),animations:'disabled'});
  await page.locator('#allTasks [data-task]').first().click();await page.locator('#modalMeta').filter({hasText:'완료'}).waitFor();await shot('task','.modal');await page.getByRole('button',{name:'닫기',exact:true}).click();
  await page.goto(base+'/#settings/slack');await page.locator('#slackConnections input').first().waitFor();await shot('slack','#panel-slack');
  await page.goto(base+'/#settings/environment');await page.locator('#vmEnabled').waitFor();await shot('environment','#panel-environment');
  await page.locator('#maintenanceOptions>summary').click();await page.getByRole('button',{name:'설치 상태 확인',exact:true}).click();await page.locator('#installationChecks').filter({hasText:'Python'}).waitFor();await shot('diagnostics','#maintenanceOptions');
  await page.locator('#maintenanceOptions details>summary').click();await shot('backup','#maintenanceOptions');
  await page.goto(base+'/#home');await page.locator('#taskOptions>summary').click();await page.locator('#taskEnvironment').selectOption('vm');await page.locator('#vmActionOptions>summary').click();await page.getByRole('button',{name:'동작 추가 +',exact:true}).click();await shot('actions','.composer');
  if(process.env.MOA_CAPTURE_SERVE_DIR){fs.mkdirSync(process.env.MOA_CAPTURE_SERVE_DIR,{recursive:true});for(const name of fs.readdirSync(output).filter(name=>name.endsWith('.png')))fs.copyFileSync(path.join(output,name),path.join(process.env.MOA_CAPTURE_SERVE_DIR,name))}
  const guideChecks=[];
  for(const guide of ['getting-started','team','slack','environment','tasks','models','operations','troubleshooting','privacy']){
   await page.goto(base+'/#docs/'+guide);await page.locator('#docContent .guide-shot img').first().waitFor();
   await page.waitForFunction(()=>{const image=document.querySelector('#docContent .guide-shot img');return image?.complete&&image.naturalWidth>0});
   const check=await page.evaluate(()=>({steps:document.querySelectorAll('#docContent>.guide-steps>li').length,detailsClosed:!document.querySelector('#docContent>.guide-more').open,overflow:document.documentElement.scrollWidth>innerWidth+1}));
   if(check.steps!==3||!check.detailsClosed||check.overflow)throw Error('Guide layout check failed: '+guide+' '+JSON.stringify(check));guideChecks.push(guide);
  }
  if(process.env.MOA_GUIDE_REVIEW_DIR){fs.mkdirSync(process.env.MOA_GUIDE_REVIEW_DIR,{recursive:true});await page.goto(base+'/#docs/getting-started');await page.locator('#docContent .guide-shot img').first().waitFor();await page.screenshot({path:path.join(process.env.MOA_GUIDE_REVIEW_DIR,'desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(process.env.MOA_GUIDE_REVIEW_DIR,'mobile.png'),fullPage:true});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Mobile guide overflow')}
  if(errors.length)throw Error(errors.join('\n'));
  fs.writeFileSync(path.join(output,'manifest.json'),JSON.stringify({version:1,kind:'actual UI screenshots with sample demo data',viewport:{width:1440,height:1040},browser:await browser.version(),guideChecks,files:fs.readdirSync(output).filter(name=>name.endsWith('.png'))},null,2));
  console.log('Captured actual UI screenshots; sample data; no Slack/model/VM connection');
 }finally{await browser.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1});

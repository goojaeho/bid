const {_electron:electron}=require('@playwright/test');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const instance=await electron.launch(process.argv.includes('--packaged')
   ? {executablePath:path.join(__dirname,'dist','v0.2.0','win-unpacked','Jarvis.exe'),args:[],env}
   : {args:[path.join(__dirname,'main.cjs')],env});
 try {
  const page=await instance.firstWindow();await page.waitForSelector('#input');
  assert.equal(await page.locator('#login').textContent(),'Google로 로그인');
  const result=await page.evaluate(()=>window.jarvis.command('브리핑해봐'));
  assert.equal(result.ok,false);
  assert.ok(result.message.includes('로그인'));
  await page.evaluate(()=>window.jarvis.command('회의하니까 안내 멈춰'));
  assert.equal((await page.evaluate(()=>window.jarvis.state())).paused,true);
  await page.evaluate(()=>window.jarvis.command('다시 시작해'));
  assert.equal((await page.evaluate(()=>window.jarvis.state())).paused,false);
  await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].close());
  assert.equal(await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isVisible()),false);
  await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].show());
  await page.screenshot({path:path.join(__dirname,'dist','jarvis-preview.png')});
  console.log('PASS: startup, login UI, signed-out command, pause/resume, close-to-tray, screenshot');
 }finally{await instance.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

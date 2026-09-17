const {_electron:electron}=require('@playwright/test');const assert=require('node:assert/strict');const path=require('node:path');
(async()=>{
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const instance=await electron.launch({executablePath:path.join(__dirname,'dist/v0.2.1/win-unpacked/Jarvis.exe'),args:['--use-fake-device-for-media-stream','--use-file-for-fake-audio-capture='+path.join(__dirname,'dist/voice-fixture.wav')],env});
 try{
  const page=await instance.firstWindow();await page.waitForSelector('#dictate');
  await page.click('#dictate');await page.waitForFunction(()=>document.getElementById('dictate').textContent.includes('녹음 끝내기'));
  await page.waitForTimeout(5000);await page.click('#dictate');
  await page.waitForFunction(()=>document.getElementById('input').value.includes('보고서'),{},{timeout:120000});
  assert.ok((await page.inputValue('#input')).includes('등록'));
  assert.equal(await page.locator('#conversation article').count(),1);
  await page.click('#dictate');await page.waitForFunction(()=>document.getElementById('dictate').textContent.includes('녹음 끝내기'));
  await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].hide());
  await page.waitForFunction(()=>document.getElementById('dictate').textContent==='마이크');
  await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].show());
  await page.screenshot({path:path.join(__dirname,'dist/voice-preview.png')});
  console.log('PASS: microphone button, real local Korean transcription, draft without auto-send, hide cancels recording');
 }finally{await instance.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

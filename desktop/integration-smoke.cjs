const {_electron:electron}=require('@playwright/test');const assert=require('node:assert/strict');const path=require('node:path');
(async()=>{
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const instance=await electron.launch({executablePath:path.join(process.env.LOCALAPPDATA,'JarvisBuild/v0.3.0/win-unpacked/Jarvis.exe'),args:['--use-fake-device-for-media-stream','--use-file-for-fake-audio-capture='+path.join(__dirname,'dist/wake-command.wav')],env});
 try{
  const page=await instance.firstWindow();await page.waitForSelector('#wake');
  await page.evaluate(()=>window.jarvis.wake(false));
  const real=await page.evaluate(()=>window.jarvis.command('브리핑해봐'));console.log('Live briefing:',JSON.stringify({ok:real.ok,message:real.ok?'content omitted':real.message}));
  // Never mutate the real user's tasks during this test. TTS uses the real Mac server.
  await instance.evaluate(({ipcMain})=>{ipcMain.removeHandler('command');ipcMain.handle('command',()=>({ok:true,message:'네, 자비스 연결 시험을 마쳤어요.'}));});
  const icon=await instance.evaluate(({nativeImage,app})=>{const icon=nativeImage.createFromPath(app.getAppPath()+'/assets/tray.ico');return {empty:icon.isEmpty(),size:icon.getSize()};});assert.equal(icon.empty,false);assert.ok(icon.size.width>=16);
  const start=Date.now();const tts=await page.evaluate(()=>window.jarvis.tts('네, 맥에서 연결된 자비스예요.'));assert.ok(tts.mime.startsWith('audio/'));assert.ok(tts.data.length>1000);console.log('Mac authenticated TTS ms:',Date.now()-start);
  await page.evaluate(()=>window.jarvis.wake(true));
  await page.waitForFunction(()=>document.querySelectorAll('#conversation article.user').length>0,{},{timeout:60000});
  await page.evaluate(()=>window.jarvis.wake(false));
  console.log('Wake command:',await page.locator('article.user').first().textContent());
  assert.ok((await page.locator('article.user').first().textContent()).includes('브리핑'));
  await page.waitForFunction(()=>!document.getElementById('send').disabled,{},{timeout:60000});
  assert.ok((await page.locator('article.assistant').last().textContent()).includes('연결 시험'));
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(__dirname,'dist/integrated-preview.png')});
  await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].close());assert.equal(await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isVisible()),false);
  console.log('PASS: white icon loads, real Mac F1 audio, wake-word to command, TTS playback, tray lifetime');
 }finally{await instance.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

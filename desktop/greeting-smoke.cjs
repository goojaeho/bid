const {_electron}=require('@playwright/test');const path=require('node:path');const assert=require('node:assert/strict');
(async()=>{const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;const app=await _electron.launch({executablePath:path.join(process.env.LOCALAPPDATA,'JarvisBuild/v0.3.2/win-unpacked/Jarvis.exe'),args:['--use-fake-device-for-media-stream','--use-file-for-fake-audio-capture='+path.join(__dirname,'dist/wake.wav')],env});
try{
 const page=await app.firstWindow();await page.waitForSelector('#wake');
 await app.evaluate(({ipcMain})=>{ipcMain.removeHandler('command');ipcMain.handle('command',()=>({ok:true,message:'시험 완료'}));});
 await page.evaluate(()=>window.jarvis.wake(true));
 await page.waitForFunction(()=>[...document.querySelectorAll('article')].some(n=>n.textContent==='네, 말씀하세요.'),{},{timeout:60000});
 assert.equal(await page.evaluate(()=>recording),false);
 await page.waitForFunction(()=>document.getElementById('dictate').textContent.includes('녹음 끝내기'),{},{timeout:20000});
 await page.evaluate(()=>window.jarvis.wake(false));await page.click('#record-cancel');
 await page.waitForFunction(()=>!recording);
 await page.evaluate(()=>acknowledgeWake());assert.equal(await page.locator('article.assistant').last().textContent(),'네, 무엇을 도와드릴까요?');
 await page.evaluate(()=>acknowledgeWake());assert.equal(await page.locator('article.assistant').last().textContent(),'네, 듣고 있어요.');
 console.log('PASS: wake greeting, recording starts after speech, rotating real F1 responses');
}finally{await app.close();}})().catch(e=>{console.error(e);process.exitCode=1;});

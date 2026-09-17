const {_electron}=require('@playwright/test');const path=require('node:path'),assert=require('node:assert/strict');
(async()=>{const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;const a=await _electron.launch({executablePath:path.join(process.env.LOCALAPPDATA,'JarvisBuild/v0.5.0/win-unpacked/Jarvis.exe'),args:['--use-fake-device-for-media-stream'],env});try{
const p=await a.firstWindow();await p.waitForSelector('#mail-refresh',{state:'attached'});
await p.evaluate(async()=>{await window.jarvis.wake(false);await window.jarvis.pause(true);window.testSpoken=[];speak=async text=>window.testSpoken.push(text);updateMail({items:[],pending:[{id:999999999,subject:'합성 알림 시험',sender:'시험',summary:'대기 요약'}],error:''});});
await new Promise(r=>setTimeout(r,2200));assert.equal(await p.evaluate(()=>window.testSpoken.length),0);
await p.evaluate(()=>window.jarvis.pause(false));await p.waitForFunction(()=>window.testSpoken.some(x=>x.includes('합성 알림 시험')),{},{timeout:5000});
await p.evaluate(()=>window.jarvis.pause(true));console.log('PASS paused mail stays silent; resume delivers summary');
}finally{await a.close();}})().catch(e=>{console.error(e);process.exitCode=1;});

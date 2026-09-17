const {_electron}=require('@playwright/test');
const path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const app=await _electron.launch({executablePath:path.join(process.env.LOCALAPPDATA,'JarvisBuild/v0.4.0/win-unpacked/Jarvis.exe'),args:['--use-fake-device-for-media-stream'],env});
 try{
  const page=await app.firstWindow();await page.waitForSelector('#wake');await page.evaluate(()=>window.jarvis.wake(false));
  for(const text of ['안녕, 넌 무엇을 도와줄 수 있어?','내일 견적서 보내야 해','그건 모레로','아니요','지금 할 일 중에 준비할 게 뭐야?']){
   const started=Date.now();const result=await page.evaluate(text=>window.jarvis.command(text),text);
   console.log(JSON.stringify({text,seconds:(Date.now()-started)/1000,result}));assert.equal(result.ok,true);
   if(text==='내일 견적서 보내야 해'||text==='그건 모레로')assert.equal(result.kind,'proposal');
  }
  // Synthetic echo-cancelled microphone input verifies interruption state transitions.
  await page.evaluate(async()=>{await window.jarvis.wake(true);await ensureMic();audio={pause(){},src:''};});
  for(let i=0;i<5;i++){await page.evaluate(()=>processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(4096).fill(0.06)}}));await new Promise(r=>setTimeout(r,70));}
  assert.equal(await page.evaluate(()=>audio===null&&interruptActive),true);
  await page.evaluate(()=>{interruptActive=false;interruptParts=[];interruptStart=0;return window.jarvis.wake(false);});
  console.log('PASS: real Mac model, proposal context, cancellation, related question; synthetic playback interruption');
 }finally{await app.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

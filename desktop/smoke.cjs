const {_electron:electron}=require('@playwright/test');
const assert=require('node:assert/strict');
const {WebSocket}=require('ws');
const path=require('node:path');
(async()=>{
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const instance=await electron.launch(process.argv.includes('--packaged')
   ? {executablePath:path.join(__dirname,'dist','win-unpacked','Jarvis.exe'),args:[],env}
   : {args:[path.join(__dirname,'main.cjs')],env});
 let socket;
 try {
  const page=await instance.firstWindow();await page.waitForSelector('#input');
  const state=await page.evaluate(()=>window.jarvis.state());
  socket=new WebSocket('ws://127.0.0.1:17843',{origin:'chrome-extension://'+'a'.repeat(32)});
  await new Promise((resolve,reject)=>{socket.once('open',resolve);socket.once('error',reject);});
  socket.send(JSON.stringify({type:'pair',token:state.pairing}));
  socket.send(JSON.stringify({type:'status',ready:true}));
  socket.on('message',raw=>{const msg=JSON.parse(raw);if(msg.type==='command')socket.send(JSON.stringify({type:'result',id:msg.id,result:{ok:true,message:'테스트 할 일 1건입니다.'}}));});
  await page.waitForFunction(()=>document.getElementById('connection').textContent==='사이트 연결됨');
  const result=await page.evaluate(()=>window.jarvis.command('브리핑해봐'));assert.equal(result.message,'테스트 할 일 1건입니다.');
  await page.evaluate(()=>window.jarvis.command('회의하니까 안내 멈춰'));
  assert.equal((await page.evaluate(()=>window.jarvis.state())).paused,true);
  await page.evaluate(()=>window.jarvis.command('다시 시작해'));
  assert.equal((await page.evaluate(()=>window.jarvis.state())).paused,false);
  await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].close());
  assert.equal(await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isVisible()),false);
  await instance.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].show());
  socket.close();await page.waitForFunction(()=>document.getElementById('connection').textContent==='브라우저 연결 필요');
  await page.screenshot({path:path.join(__dirname,'dist','jarvis-preview.png')});
  console.log('PASS: startup, authenticated bridge roundtrip, pause/resume, close-to-tray, disconnect, screenshot');
 }finally{if(socket)socket.close();await instance.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

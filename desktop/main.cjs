const { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, shell, safeStorage } = require('electron');
const { Account } = require('./account.cjs');
const { Speech } = require('./speech.cjs');
const { Assistant } = require('./assistant.cjs');
const fs = require('node:fs');
const {privateDecrypt,constants}=require('node:crypto');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { localCommand, validateTts } = require('./logic.cjs');
let win, tray, account, speech, quitting=false, paused=false, settings={}, pending=false;
let ttsToken='',ttsOrigin='',ttsCache=new Map(),ttsQueue=Promise.resolve();
const page=pathToFileURL(path.join(__dirname,'index.html')).href;
function state() { return {connected:!!account?.token, paused, ttsUrl:settings.ttsUrl||'', wakeEnabled:!!settings.wakeEnabled, autoStart:app.getLoginItemSettings().openAtLogin}; }
function emit() { if(win && !win.isDestroyed()) win.webContents.send('state',state()); }
function show() { win.show(); win.focus(); }
function check(event) { if(event.sender!==win.webContents || event.senderFrame.url!==page) throw new Error('허용되지 않은 요청'); }
function menu() {
  tray.setContextMenu(Menu.buildFromTemplate([
    {label:'자비스 열기',click:show},
    {label:paused?'음성 안내 재개':'음성 안내 멈춤',click:()=>{paused=!paused; emit(); menu();}},
    {label:'기존 사이트 열기',click:()=>shell.openExternal('https://www.oneaigen.com/jarvis')},
    {type:'separator'},{label:'자비스 종료',click:()=>app.quit()}
  ]));
}
if(!app.requestSingleInstanceLock()) app.quit();
else {
app.on('second-instance',()=>{if(win)show();});
app.whenReady().then(()=>{
  const config=path.join(app.getPath('userData'),'settings.json');
  try{settings=JSON.parse(fs.readFileSync(config,'utf8'));}catch{}
  const tokenFile=path.join(app.getPath('userData'),'tts-token.bin');
  const metadataFile=path.join(app.getPath('userData'),'tts-origin.json');
  const pairingFile=path.join(app.getPath('userData'),'tts-pairing.json');
  const privateFile=path.join(app.getPath('userData'),'tts-private.pem');
  try {
    if(fs.existsSync(pairingFile)){
      const pair=JSON.parse(fs.readFileSync(pairingFile,'utf8'));
      const secret=privateDecrypt({key:fs.readFileSync(privateFile),padding:constants.RSA_PKCS1_OAEP_PADDING,oaepHash:'sha256'},Buffer.from(pair.ciphertext,'base64')).toString('utf8');
      fs.writeFileSync(tokenFile,safeStorage.encryptString(secret));
      fs.writeFileSync(metadataFile,JSON.stringify({origin:new URL(pair.url).origin}));
      settings.ttsUrl=pair.url;
      if(settings.wakeEnabled===undefined)settings.wakeEnabled=true;
      fs.writeFileSync(config,JSON.stringify(settings));
      fs.unlinkSync(pairingFile);fs.unlinkSync(privateFile);
    }
    ttsToken=safeStorage.decryptString(fs.readFileSync(tokenFile));
    ttsOrigin=JSON.parse(fs.readFileSync(metadataFile,'utf8')).origin;
  }catch{}

  win=new BrowserWindow({width:880,height:790,minWidth:600,minHeight:650,show:false,backgroundColor:'#101321',title:'자비스',webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,backgroundThrottling:false,autoplayPolicy:'no-user-gesture-required'}});
  win.removeMenu();
  win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  win.webContents.on('will-navigate',event=>event.preventDefault());
  win.webContents.session.setPermissionRequestHandler((contents,permission,cb,details)=>cb(
    contents===win.webContents && contents.getURL()===page && permission==='media' &&
    details.mediaTypes?.includes('audio') && !details.mediaTypes?.includes('video')));
  win.webContents.session.setPermissionCheckHandler((contents,permission)=>
    contents===win.webContents && contents.getURL()===page && permission==='media');
  win.on('hide',()=>{if(!settings.wakeEnabled)win.webContents.send('stop-recording');});
  win.on('close',e=>{if(!quitting){e.preventDefault();win.hide();}});
  win.loadFile('index.html'); win.once('ready-to-show',show);
  const icon=nativeImage.createFromPath(path.join(__dirname,'assets','tray.ico'));
  if(icon.isEmpty())throw new Error('Tray icon failed to load');
  tray=new Tray(icon);tray.setToolTip('자비스 — 클릭해서 열기');tray.on('click',show);menu();
  account=new Account({directory:app.getPath('userData'),crypto:safeStorage,open:url=>shell.openExternal(url),onChange:notice=>{emit();if(notice)win.webContents.send('notice',notice);else show();}});
  account.load();
  const assistant=new Assistant({execute:text=>account.command(text),infer:async body=>{
    if(!ttsToken||!ttsOrigin)throw Error('맥 대화 서버 연결 설정이 필요해요.');
    const url=new URL('/assistant',ttsOrigin);url.port='8766';
    let response;
    for(let attempt=0;attempt<3;attempt++){
      response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+ttsToken},body:JSON.stringify(body),signal:AbortSignal.timeout(90000),redirect:'error'});
      if(response.status!==429||attempt===2)break;
      const delay=Math.min(5,Math.max(1,Number(response.headers.get('retry-after'))||2));
      await response.body?.cancel();await new Promise(resolve=>setTimeout(resolve,delay*1000));
    }
    if(!response.ok)throw Error(response.status===429?'맥이 앞선 요청을 처리 중이에요. 잠시 뒤 말씀해주세요.':'맥 대화 서버에 연결하지 못했어요.');
    const data=await response.text();if(data.length>20000)throw Error('대화 응답이 너무 길어요.');return JSON.parse(data);
  }});
  const speechRoot=app.isPackaged?process.resourcesPath:__dirname;
  try { speech=new Speech(JSON.parse(fs.readFileSync(path.join(speechRoot,'speech-runtime.json'),'utf8')),path.join(speechRoot,'transcribe.py')); } catch {}
  ipcMain.handle('transcribe',async(e,bytes,mode)=>{check(e);if(!speech)return {error:'음성 인식 환경이 준비되지 않았어요.'};try{return await speech.transcribe(bytes,mode==='wake'?'wake':'command');}catch(error){return {error:error.message};}});
  ipcMain.handle('wake', (e,value)=>{check(e);settings.wakeEnabled=!!value;fs.writeFileSync(config,JSON.stringify(settings));emit();return state();});
  ipcMain.handle('show', e=>{check(e);show();});
  ipcMain.handle('state',e=>{check(e);return state();});
  ipcMain.handle('login',async e=>{check(e);await account.login();return {ok:true};});
  ipcMain.handle('logout',e=>{check(e);assistant.reset();account.logout();return state();});
  ipcMain.handle('site',e=>{check(e);return shell.openExternal('https://www.oneaigen.com/jarvis');});
  ipcMain.handle('pause',(e,value)=>{check(e);paused=!!value;emit();menu();return state();});
  ipcMain.handle('settings',(e,value)=>{
    check(e);settings.ttsUrl=validateTts(String(value.ttsUrl||'').trim());
    fs.writeFileSync(config,JSON.stringify(settings));
    if(app.isPackaged)app.setLoginItemSettings({openAtLogin:!!value.autoStart});
    emit();return state();
  });
  ipcMain.handle('command',async(e,text)=>{
    check(e);if(typeof text!=='string'||!text.trim()||text.length>250)return {ok:false,message:'명령을 250자 이내로 말씀해주세요.'};
    const local=localCommand(text.trim());
    if(local){paused=local==='pause';emit();menu();return {ok:true,message:paused?'네, 음성 안내를 멈췄어요.':'네, 다시 안내할게요. 현재 이 앱에는 메일 알림이 연결되지 않아 밀린 메일 요약은 아직 없어요.'};}
    if(pending)return {ok:false,message:'앞선 요청을 처리 중이에요.'};
    pending=true;
    try{return await assistant.command(text);}catch(error){return {ok:false,message:error.message||'맥 대화 서버를 확인해주세요.'};}finally{pending=false;}
  });

  ipcMain.handle('tts',(e,text)=>{
    check(e);const job=ttsQueue.then(()=>synthesize(text));ttsQueue=job.catch(()=>{});return job;
  });
  async function synthesize(text){
    if(paused||!settings.ttsUrl)return null;
    if(typeof text!=='string'||text.length>12000)throw new Error('잘못된 음성 요청');
    const url=validateTts(settings.ttsUrl),cacheKey=url+'|'+text;
    if(ttsCache.has(cacheKey))return ttsCache.get(cacheKey);
    const headers={'Content-Type':'application/json'};
    if(ttsToken&&new URL(url).origin===ttsOrigin)headers.Authorization='Bearer '+ttsToken;
    const response=await fetch(url,{method:'POST',headers,body:JSON.stringify({text,language:'ko'}),signal:AbortSignal.timeout(30000),redirect:'error'});
    if(!response.ok)throw new Error('음성 서버 응답 오류');
    const mime=response.headers.get('content-type')||'';
    if(!mime.startsWith('audio/'))throw new Error('음성 서버가 오디오를 반환하지 않았습니다.');
    const chunks=[];let size=0;
    for await(const chunk of response.body){size+=chunk.length;if(size>20*1024*1024)throw new Error('음성 파일이 너무 큽니다.');chunks.push(chunk);}
    if(paused)return null;
    const result={mime,data:Buffer.concat(chunks).toString('base64')};
    if(size<1024*1024){if(ttsCache.size>=10)ttsCache.delete(ttsCache.keys().next().value);ttsCache.set(cacheKey,result);}
    return result;
  }
});
app.on('before-quit',()=>{quitting=true;if(account)account.cancel();if(speech)speech.close();});
app.on('window-all-closed',()=>{});
}

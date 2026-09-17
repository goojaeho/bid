const { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, shell, safeStorage } = require('electron');
const { Account } = require('./account.cjs');
const { Speech } = require('./speech.cjs');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { localCommand, validateTts } = require('./logic.cjs');
let win, tray, account, speech, quitting=false, paused=false, settings={}, pending=false;
const page=pathToFileURL(path.join(__dirname,'index.html')).href;
function state() { return {connected:!!account?.token, paused, ttsUrl:settings.ttsUrl||'', autoStart:app.getLoginItemSettings().openAtLogin}; }
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
  win=new BrowserWindow({width:880,height:790,minWidth:600,minHeight:650,show:false,backgroundColor:'#101321',title:'자비스',webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,backgroundThrottling:false}});
  win.removeMenu();
  win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  win.webContents.on('will-navigate',event=>event.preventDefault());
  win.webContents.session.setPermissionRequestHandler((contents,permission,cb,details)=>cb(
    contents===win.webContents && contents.getURL()===page && permission==='media' &&
    details.mediaTypes?.includes('audio') && !details.mediaTypes?.includes('video')));
  win.webContents.session.setPermissionCheckHandler((contents,permission)=>
    contents===win.webContents && contents.getURL()===page && permission==='media');
  win.on('hide',()=>win.webContents.send('stop-recording'));
  win.on('close',e=>{if(!quitting){e.preventDefault();win.hide();}});
  win.loadFile('index.html'); win.once('ready-to-show',show);
  const icon=nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAIElEQVQ4T2NkYPj/n4ECwESJ5lEDRg0YNWDUgFEDBgYAAGkCH+F7mVIAAAAASUVORK5CYII=');
  tray=new Tray(icon);tray.setToolTip('자비스 — 클릭해서 열기');tray.on('click',show);menu();
  account=new Account({directory:app.getPath('userData'),crypto:safeStorage,open:url=>shell.openExternal(url),onChange:notice=>{emit();if(notice)win.webContents.send('notice',notice);else show();}});
  account.load();
  const speechRoot=app.isPackaged?process.resourcesPath:__dirname;
  try { speech=new Speech(JSON.parse(fs.readFileSync(path.join(speechRoot,'speech-runtime.json'),'utf8')),path.join(speechRoot,'transcribe.py')); } catch {}
  ipcMain.handle('transcribe',async(e,bytes)=>{check(e);if(!speech)return {error:'음성 인식 환경이 준비되지 않았어요.'};try{return await speech.transcribe(bytes);}catch(error){return {error:error.message};}});
  ipcMain.handle('state',e=>{check(e);return state();});
  ipcMain.handle('login',async e=>{check(e);await account.login();return {ok:true};});
  ipcMain.handle('logout',e=>{check(e);account.logout();return state();});
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
    try{return await account.command(text);}finally{pending=false;}
  });

  ipcMain.handle('tts',async(e,text)=>{
    check(e);if(paused||!settings.ttsUrl)return null;
    if(typeof text!=='string'||text.length>12000)throw new Error('잘못된 음성 요청');
    const response=await fetch(validateTts(settings.ttsUrl),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,language:'ko'}),signal:AbortSignal.timeout(30000),redirect:'error'});
    if(!response.ok)throw new Error('음성 서버 응답 오류');
    const mime=response.headers.get('content-type')||'';
    if(!mime.startsWith('audio/'))throw new Error('음성 서버가 오디오를 반환하지 않았습니다.');
    const chunks=[];let size=0;
    for await(const chunk of response.body){size+=chunk.length;if(size>20*1024*1024)throw new Error('음성 파일이 너무 큽니다.');chunks.push(chunk);}
    if(paused)return null;
    return {mime,data:Buffer.concat(chunks).toString('base64')};
  });
});
app.on('before-quit',()=>{quitting=true;if(account)account.cancel();if(speech)speech.close();});
app.on('window-all-closed',()=>{});
}

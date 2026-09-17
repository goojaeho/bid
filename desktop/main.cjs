const { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, shell, clipboard } = require('electron');
const { WebSocketServer, WebSocket } = require('ws');
const { randomBytes, randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { localCommand, validateTts } = require('./logic.cjs');
let win, tray, bridge, peer, quitting=false, paused=false, settings={}, pending=null, ready=false;
const token=randomBytes(24).toString('hex');
const page=pathToFileURL(path.join(__dirname,'index.html')).href;
function state() { return {connected:!!peer && ready, paused, ttsUrl:settings.ttsUrl||'', autoStart:app.getLoginItemSettings().openAtLogin, pairing:token}; }
function emit() { if(win && !win.isDestroyed()) win.webContents.send('state',state()); }
function show() { win.show(); win.focus(); }
function failPending(message) { if(pending) { clearTimeout(pending.timer); pending.resolve({ok:false,message}); pending=null; } }
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
  win.webContents.session.setPermissionRequestHandler((_w,_p,cb)=>cb(false));
  win.on('close',e=>{if(!quitting){e.preventDefault();win.hide();}});
  win.loadFile('index.html'); win.once('ready-to-show',show);
  const icon=nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAIElEQVQ4T2NkYPj/n4ECwESJ5lEDRg0YNWDUgFEDBgYAAGkCH+F7mVIAAAAASUVORK5CYII=');
  tray=new Tray(icon);tray.setToolTip('자비스 — 클릭해서 열기');tray.on('click',show);menu();
  bridge=new WebSocketServer({host:'127.0.0.1',port:17843,maxPayload:65536});
  bridge.on('error',()=>win.webContents.send('notice','연결 포트 17843을 사용할 수 없습니다. 다른 자비스 앱이 실행 중인지 확인해주세요.'));
  bridge.on('connection',(socket,req)=>{
    if(!/^chrome-extension:\/\/[a-p]{32}$/.test(req.headers.origin||'')){socket.close();return;}
    let authenticated=false;
    const deadline=setTimeout(()=>socket.close(),4000);
    socket.on('message',raw=>{
      let data;try{data=JSON.parse(raw);}catch{return socket.close();}
      if(!authenticated){
        if(data.type!=='pair'||data.token!==token){socket.close();return;}
        authenticated=true;clearTimeout(deadline);if(peer)peer.close();peer=socket;ready=false;emit();return;
      }
      if(socket!==peer)return;
      if(data.type==='status'){ready=data.ready===true;emit();}
      if(data.type==='result'&&pending&&data.id===pending.id){
        clearTimeout(pending.timer);pending.resolve({ok:data.result?.ok===true,message:String(data.result?.message||'응답을 확인하지 못했습니다.').slice(0,12000)});pending=null;
      }
    });
    socket.on('close',()=>{clearTimeout(deadline);if(peer===socket){peer=null;ready=false;failPending('브라우저 연결이 끊겼어요. 등록 여부는 사이트에서 확인해주세요.');emit();}});
  });
  ipcMain.handle('state',e=>{check(e);return state();});
  ipcMain.handle('copy-pairing',e=>{check(e);clipboard.writeText(token);});
  ipcMain.handle('extension',e=>{check(e);return shell.openPath(app.isPackaged?path.join(process.resourcesPath,'extension'):path.join(__dirname,'extension'));});
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
    if(!peer||!ready)return {ok:false,message:'확장 기능을 연결하고 브라우저에서 자비스 사이트를 열어주세요.'};
    return new Promise(resolve=>{
      const id=randomUUID();pending={id,resolve,timer:setTimeout(()=>failPending('응답 시간이 초과됐어요. 중복 등록을 피하려면 사이트에서 먼저 확인해주세요.'),25000)};
      peer.send(JSON.stringify({type:'command',id,text}));
    });
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
app.on('before-quit',()=>{quitting=true;failPending('앱을 종료합니다.');if(peer)peer.close();if(bridge)bridge.close();});
app.on('window-all-closed',()=>{});
}

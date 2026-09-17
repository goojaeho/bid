const { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, shell, safeStorage } = require('electron');
const { Account } = require('./account.cjs');
const { Speech } = require('./speech.cjs');
const { Assistant,todayKst,calendarLine } = require('./assistant.cjs');
const { Records } = require('./records.cjs');
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
  const service=(op,payload)=>account.service(op,payload);
  const records=new Records(service);
  const assistant=new Assistant({execute:text=>account.command(text),service,records,infer:async body=>{
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
  const mailFile=path.join(app.getPath('userData'),'mail-state.bin');
  let mailState={seen:[],pending:[],initialized:false},mailItems=[],polling=false,mailCursor='',mailError='';
  try{mailState=JSON.parse(safeStorage.decryptString(fs.readFileSync(mailFile)));}catch{}
  if(!mailState.since)mailState.since=Date.now();
  const persistMail=()=>fs.writeFileSync(mailFile,safeStorage.encryptString(JSON.stringify(mailState)));
  const emitMail=()=>{if(!win.isDestroyed())win.webContents.send('mail-feed',{items:mailItems,pending:mailState.pending,error:mailError});};
  async function pollMail(){
    if(polling||!account.token)return;polling=true;
    try{const r=await service('mail.poll',{cursor:mailCursor});if(!r.ok)throw Error(r.message);
      mailCursor=r.next_cursor||'';mailError='';mailItems=r.items;
      const known=new Set(mailState.seen);
      if(mailState.initialized)for(const m of r.items)if(!known.has(m.id)&&m.category!=='promo'&&Date.parse(m.received_at)>=mailState.since)mailState.pending.push({id:m.id,subject:m.subject,summary:m.summary||m.snippet,sender:m.sender});
      mailState.seen=[...new Set([...mailState.seen,...r.items.map(x=>x.id)])].slice(-10000);mailState.initialized=true;
      persistMail();if(r.new)records.invalidate();
    }catch(e){mailError=e.message;}finally{polling=false;emitMail();}
  }
  const pollTimer=setInterval(pollMail,60000);setTimeout(pollMail,8000);
  app.on('before-quit',()=>clearInterval(pollTimer));
  async function exclusive(job){if(pending)return {ok:false,message:'앞선 요청을 처리 중이에요.'};pending=true;try{return await job();}finally{pending=false;}}
  const trashTokens=new Map();
  ipcMain.handle('mail-refresh',async e=>{check(e);await pollMail();return {items:mailItems,pending:mailState.pending,error:mailError};});
  ipcMain.handle('mail-ack',(e,ids)=>{check(e);if(!Array.isArray(ids))return;mailState.pending=mailState.pending.filter(x=>!ids.includes(x.id));persistMail();});
  ipcMain.handle('mail-action',async(e,action,id)=>{
    check(e);if(!Number.isSafeInteger(id)||id<1)throw Error('잘못된 메일');
    if(action==='event-auto'&&assistant.proposal)return {ok:false,message:'먼저 진행 중인 제안을 확인해주세요.'};
    if(action==='event'||action==='event-auto'){const r=await service('mail.detail',{id});if(!r.ok)return r;return exclusive(async()=>{const proposal=await assistant.mailEvent(r.item);if(proposal.schedule){const saved=await service('mail.analysis',{id,schedule:proposal.schedule});if(saved.ok)records.invalidate();}return proposal;});}
    if(action==='read')return service('mail.detail',{id});
    if(action==='trash-preview'){const r=await service('mail.trash.preview',{id});if(r.ok)trashTokens.set(id,r.token);return {ok:r.ok,message:r.ok?`‘${r.subject}’ 메일을 휴지통으로 옮길까요?`:r.message};}
    if(action==='trash'){const token=trashTokens.get(id);if(!token)return {ok:false,message:'먼저 삭제할 메일을 확인해주세요.'};trashTokens.delete(id);const r=await service('mail.trash.commit',{token});if(r.ok){records.invalidate();await pollMail();}return r;}
    throw Error('지원하지 않는 메일 작업');
  });
  ipcMain.handle('calendar-list',async e=>{check(e);const start=todayKst()+'T00:00:00+09:00';const end=new Date(Date.parse(start)+7*86400000).toISOString();const r=await service('calendar.list',{start,end});return {...r,events:r.items,message:r.ok?(r.items.length?r.items.map(calendarLine).join('\n'):'앞으로 7일간 일정이 없어요.'):r.message};});
  ipcMain.handle('calendar-preview',(e,schedule,id)=>{check(e);return exclusive(()=>assistant.prepareEvent(schedule,id||''));});
  ipcMain.handle('proposal-approve',(e,id)=>{check(e);if(typeof id!=='string')throw Error('승인할 제안을 확인해주세요.');return exclusive(()=>assistant.approve(id));});
  ipcMain.handle('records-search',(e,query)=>{check(e);if(typeof query!=='string'||query.length>250)throw Error('검색어를 확인해주세요.');return records.search(query);});
  ipcMain.handle('open-link',(e,value)=>{check(e);const u=new URL(value);if(u.protocol!=='https:'||!['www.oneaigen.com','calendar.google.com','www.google.com'].includes(u.hostname))throw Error('허용되지 않은 링크');return shell.openExternal(u.href);});
  ipcMain.handle('google-connect',e=>{check(e);return shell.openExternal('https://www.oneaigen.com/gmail/connect');});
  const speechRoot=app.isPackaged?process.resourcesPath:__dirname;
  try { speech=new Speech(JSON.parse(fs.readFileSync(path.join(speechRoot,'speech-runtime.json'),'utf8')),path.join(speechRoot,'transcribe.py')); } catch {}
  ipcMain.handle('transcribe',async(e,bytes,mode)=>{check(e);if(!speech)return {error:'음성 인식 환경이 준비되지 않았어요.'};try{return await speech.transcribe(bytes,mode==='wake'?'wake':'command');}catch(error){return {error:error.message};}});
  ipcMain.handle('wake', (e,value)=>{check(e);settings.wakeEnabled=!!value;fs.writeFileSync(config,JSON.stringify(settings));emit();return state();});
  ipcMain.handle('show', e=>{check(e);show();});
  ipcMain.handle('state',e=>{check(e);return state();});
  ipcMain.handle('login',async e=>{check(e);await account.login();return {ok:true};});
  ipcMain.handle('logout',e=>{check(e);assistant.reset();records.items=[];records.invalidate();account.logout();mailState={seen:[],pending:[],initialized:false,since:Date.now()};mailItems=[];persistMail();emitMail();return state();});
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
    if(local){paused=local==='pause';emit();menu();return {ok:true,message:paused?'네, 음성 안내를 멈췄어요.':'네, 다시 안내할게요. 밀린 메일이 있으면 요약해서 알려드릴게요.'};}
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

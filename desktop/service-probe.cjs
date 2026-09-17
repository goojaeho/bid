const {app,safeStorage}=require('electron');const fs=require('fs'),path=require('path');
app.setPath('userData',path.join(process.env.APPDATA,'jarvis-desktop'));
app.whenReady().then(async()=>{try{
 const dir=path.join(process.env.APPDATA,'jarvis-desktop');const token=safeStorage.decryptString(fs.readFileSync(path.join(dir,'account.bin')));
 const op=process.argv[2]||'status';
 const payload=process.argv[3]?(op==='records'?{source:process.argv[3]}:JSON.parse(fs.readFileSync(process.argv[3],'utf8'))):{};
 const r=await fetch('https://www.oneaigen.com/api/desktop/service',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({op,...payload}),signal:AbortSignal.timeout(60000)});
 const data=await r.json();
 if(op==='records')console.log(JSON.stringify({status:r.status,ok:data.ok,count:data.items?.length,next:data.next,message:data.message}));
 else if(op==='mail.poll')console.log(JSON.stringify({status:r.status,ok:data.ok,count:data.items?.length,new:data.new,backlog:data.backlog,message:data.message}));
 else console.log(JSON.stringify({status:r.status,...data}));
 }catch(e){console.error(e.message);process.exitCode=1;}finally{app.quit();}});

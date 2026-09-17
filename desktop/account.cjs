const http=require('node:http');
const {randomBytes,createHash}=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const SITE='https://www.oneaigen.com';
class Account {
 constructor({directory,crypto,open,onChange,request=fetch}){this.file=path.join(directory,'account.bin');this.crypto=crypto;this.open=open;this.onChange=onChange;this.request=request;this.token='';this.server=null;this.timer=null;}
 load(){try{this.token=this.crypto.decryptString(fs.readFileSync(this.file));}catch{this.token='';}}
 logout(){this.cancel();this.token='';try{fs.unlinkSync(this.file);}catch{}this.onChange();}
 cancel(){clearTimeout(this.timer);if(this.server){this.server.close();this.server=null;}}
 async post(route,body,token=''){
  const response=await this.request(SITE+route,{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify(body),signal:AbortSignal.timeout(25000),redirect:'error'});
  const data=await response.json();return {status:response.status,data};
 }
 async login(){
  this.cancel();
  if(!this.crypto.isEncryptionAvailable())throw Error('Windows 로그인 정보 보호 기능을 사용할 수 없어요.');
  const nonce=randomBytes(32).toString('hex'),verifier=randomBytes(32).toString('base64url');
  const challenge=createHash('sha256').update(verifier).digest('base64url');
  let accepting=true;
  const server=http.createServer(async(req,res)=>{
   res.setHeader('Content-Type','text/html; charset=utf-8');res.setHeader('Cache-Control','no-store');res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Content-Security-Policy',"default-src 'none'");
   const u=new URL(req.url,'http://127.0.0.1');
   if(req.method!=='GET'||u.pathname!=='/callback'||u.searchParams.get('state')!==nonce||!accepting){res.writeHead(400);res.end('잘못된 로그인 요청입니다.');return;}
   accepting=false;
   try{
    const {status,data}=await this.post('/api/desktop/token',{code:u.searchParams.get('code'),verifier});
    if(status!==200||typeof data.token!=='string'||this.server!==server)throw Error();
    fs.writeFileSync(this.file,this.crypto.encryptString(data.token));this.token=data.token;
    res.end('<h2>자비스 로그인이 완료됐어요.</h2><p>이 창을 닫고 자비스 앱으로 돌아가세요.</p>');this.onChange();
   }catch{res.writeHead(400);res.end('로그인을 완료하지 못했어요. 앱에서 다시 시도해주세요.');this.onChange('로그인을 완료하지 못했어요. 다시 시도해주세요.');}
   finally{if(this.server===server)this.cancel();else server.close();}
  });
  this.server=server;
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  this.timer=setTimeout(()=>{this.cancel();this.onChange('로그인 대기 시간이 지났어요. 다시 로그인해주세요.');},300000);
  try{
   const {status,data}=await this.post('/api/desktop/start',{port:server.address().port,nonce,challenge});
   if(status!==200)throw Error('로그인 서버에 연결하지 못했어요.');
   const url=new URL(data.url);if(url.origin!=='https://accounts.google.com')throw Error('잘못된 로그인 주소');
   await this.open(url.href);
  }catch(error){this.cancel();throw error;}
 }
 async command(text){
  if(!this.token)return {ok:false,message:'처음 한 번 Google로 로그인해주세요.'};
  try{const {status,data}=await this.post('/api/desktop/command',{text},this.token);if(status===401)this.logout();return data;}
  catch{return {ok:false,message:'서버 응답을 확인하지 못했어요. 중복 등록을 피하려면 기존 사이트에서 등록 여부를 확인해주세요.'};}
 }
 async service(op,payload={}){
  if(!this.token)return {ok:false,message:'먼저 Google로 로그인해주세요.'};
  const {status,data}=await this.post('/api/desktop/service',{...payload,op},this.token);
  if(status===401)this.logout();return data;
 }
}
module.exports={Account};

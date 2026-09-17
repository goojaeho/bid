const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const {Account}=require('./account.cjs');
test('system-browser login, nonce rejection, persisted credential and logout',async()=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'jarvis-test-'));let start;
 const crypto={isEncryptionAvailable:()=>true,encryptString:t=>Buffer.from('encrypted:'+t),decryptString:b=>b.toString().slice(10)};
 const request=async(url,options)=>{const body=JSON.parse(options.body);if(url.endsWith('/start')){start=body;return {status:200,json:async()=>({url:'https://accounts.google.com/o/oauth2/v2/auth'})};}if(url.endsWith('/token'))return {status:200,json:async()=>({token:'fixture-token'})};assert.equal(options.headers.Authorization,'Bearer fixture-token');return {status:200,json:async()=>({ok:true,message:'fixture'})};};
 const account=new Account({directory,crypto,request,open:async()=>{},onChange:()=>{}});
 try{
  await account.login();
  const bad=await fetch(`http://127.0.0.1:${start.port}/callback?state=wrong&code=x`);assert.equal(bad.status,400);assert.equal(account.token,'');
  const good=await fetch(`http://127.0.0.1:${start.port}/callback?state=${start.nonce}&code=fixture`);assert.equal(good.status,200);
  const second=new Account({directory,crypto,request,open:async()=>{},onChange:()=>{}});second.load();assert.equal(second.token,'fixture-token');assert.equal((await second.command('브리핑해봐')).ok,true);second.logout();assert.equal(second.token,'');assert.equal(fs.existsSync(second.file),false);
 }finally{account.cancel();fs.rmSync(directory,{recursive:true,force:true});}
});

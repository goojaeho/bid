const {spawn}=require('node:child_process');
class Speech {
 constructor(config,script){this.config=config;this.script=script;this.child=null;}
 transcribe(bytes){
  if(this.child)return Promise.reject(Error('이미 음성을 변환하고 있어요.'));
  if(!(bytes instanceof Uint8Array)||!bytes.length||bytes.length>12*1024*1024)return Promise.reject(Error('녹음 파일을 확인해주세요.'));
  return new Promise((resolve,reject)=>{
   const child=spawn(this.config.python,[this.script,this.config.model],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,HF_HUB_OFFLINE:'1',PYTHONIOENCODING:'utf-8'}});this.child=child;
   let output='',settled=false;
   const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);if(this.child===child)this.child=null;error?reject(error):resolve(value);};
   const timer=setTimeout(()=>{child.kill();finish(Error('음성 변환 시간이 길어졌어요. 더 짧게 녹음해주세요.'));},120000);
   child.stdout.on('data',data=>{output+=data.toString();if(output.length>64000){child.kill();finish(Error('변환 결과가 너무 길어요.'));}});
   child.stderr.resume();child.stdin.on('error',()=>{});
   child.on('error',()=>finish(Error('음성 인식 프로그램을 실행하지 못했어요.')));
   child.on('close',code=>{if(code!==0)return finish(Error('음성을 변환하지 못했어요. 다시 녹음해주세요.'));try{const value=JSON.parse(output);if(typeof value.text!=='string')throw Error();finish(null,value);}catch{finish(Error('음성 인식 결과를 확인하지 못했어요.'));}});
   child.stdin.end(Buffer.from(bytes));
  });
 }
 close(){if(this.child)this.child.kill();}
}
module.exports={Speech};

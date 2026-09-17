const {spawn}=require('node:child_process');
class Speech {
 constructor(config,script){this.config=config;this.script=script;this.child=null;this.pending=null;this.next=0;this.start();}
 start(){
  if(this.child)return;
  const child=spawn(this.config.python,['-u',this.script,this.config.model,this.config.wakeModel||this.config.model],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,HF_HUB_OFFLINE:'1',PYTHONIOENCODING:'utf-8'}});this.child=child;
  let output='';
  child.stdout.on('data',chunk=>{output+=chunk.toString();if(output.length>64000){child.kill();return;}
   let index;while((index=output.indexOf('\n'))>=0){const line=output.slice(0,index);output=output.slice(index+1);try{const data=JSON.parse(line);if(this.pending&&data.id===this.pending.id){const p=this.pending;this.pending=null;clearTimeout(p.timer);data.error?p.reject(Error(data.error)):p.resolve(data);}}catch{}}
  });
  child.stderr.resume();child.stdin.on('error',()=>{});
  const ended=()=>{if(this.child===child)this.child=null;if(this.pending){clearTimeout(this.pending.timer);this.pending.reject(Error('음성 인식 연결이 끊겼어요. 다시 시도해주세요.'));this.pending=null;}};
  child.on('error',ended);child.on('close',ended);
 }
 transcribe(bytes,mode="command"){
  if(this.pending)return Promise.reject(Error('이미 음성을 변환하고 있어요.'));
  if(!(bytes instanceof Uint8Array)||!bytes.length||bytes.length>12*1024*1024)return Promise.reject(Error('녹음을 확인해주세요.'));
  this.start();
  return new Promise((resolve,reject)=>{const id=++this.next;
   const timer=setTimeout(()=>{this.close();},120000);
   this.pending={id,resolve,reject,timer};this.child.stdin.write(JSON.stringify({id,mode,audio:Buffer.from(bytes).toString('base64')})+'\n');
  });
 }
 close(){if(this.child)this.child.kill();}
}
module.exports={Speech};

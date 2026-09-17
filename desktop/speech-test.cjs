const {Speech}=require('./speech.cjs');const {wakeCommand,wav}=require('./voice-utils.js');const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
(async()=>{const worker=new Speech(JSON.parse(fs.readFileSync(path.join(__dirname,'speech-runtime.json'),'utf8')),path.join(__dirname,'transcribe.py'));
 try{
  for(const name of ['voice-fixture','voice-fixture','wake','wake-command','negative']){
   const start=performance.now();const result=await worker.transcribe(new Uint8Array(fs.readFileSync(path.join(__dirname,'dist',name+'.wav'))),name.startsWith('wake')||name==='negative'?'wake':'command');
   console.log(JSON.stringify({sample:name,text:result.text,modelSeconds:result.seconds,totalSeconds:Math.round((performance.now()-start)/10)/100}));
   if(name==='voice-fixture')assert.ok(result.text.includes('보고서'));
   if(name==='wake'||name==='wake-command')assert.notEqual(wakeCommand(result.text),null);
   if(name==='negative')assert.equal(wakeCommand(result.text),null);
  }
  const silence=wav([new Float32Array(16000)],16000);const result=await worker.transcribe(silence);assert.equal(result.text,'');console.log('PASS: silence does not trigger');
 }finally{worker.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

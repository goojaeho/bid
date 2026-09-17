let micPromise=null;
let recorder=null,micStream=null,recordTimer=null,recordStarting=false,cancelRecording=false;
let context=null,analyser=null,processor=null,source=null,silentGain=null,animation=null;
let wakeProcessing=false,wakeParts=[],preRoll=[],speechStart=0,lastLoud=0,commandAuto=false,commandSpoke=false,commandStarted=0,lastCommandLoud=0;
let interruptParts=[],interruptStart=0,interruptLast=0,interruptActive=false;
async function finishInterrupt(parts,rate){
 transcribing=true;recordingControls();
 try{const result=await window.jarvis.transcribe(VoiceUtils.wav(parts,rate),'command');
  if(result.error)throw Error(result.error);
  const text=(result.text||'').trim().replace(/^자비스[야,\s]*/,'');
  transcribing=false;if(text&&text.length<=250)await send(text);
 }catch{$('status').textContent='다시 말씀해주세요.';}finally{transcribing=false;recordingControls();}
}
function recordingControls(){ $('dictate').textContent=transcribing?'글로 바꾸는 중…':recording?'■ 녹음 끝내기':'마이크';$('dictate').disabled=transcribing||recordStarting;$('send').disabled=recording||transcribing;$('brief').disabled=recording||transcribing;$('record-cancel').disabled=!recording&&!recordStarting;$('record-stop').disabled=!recording; }
function resetWake(){wakeParts=[];preRoll=[];speechStart=0;lastLoud=0;}
let waveHistory=[],waveTick=0;
function paintWave(level=0,advance=false){
 const canvas=$('waveform'),width=canvas.clientWidth,height=32,ratio=window.devicePixelRatio||1;
 if(width<1)return;
 if(canvas.width!==Math.round(width*ratio)||canvas.height!==Math.round(height*ratio)){canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);}
 const count=Math.max(1,Math.floor(width/6));while(waveHistory.length<count)waveHistory.unshift(0);if(waveHistory.length>count)waveHistory=waveHistory.slice(-count);
 if(advance){waveHistory.shift();waveHistory.push(level);}
 const g=canvas.getContext('2d');g.setTransform(ratio,0,0,ratio,0,0);g.clearRect(0,0,width,height);g.lineCap='round';g.lineWidth=2.6;
 for(let i=0;i<count;i++){const amplitude=waveHistory[i],bar=Math.max(0,Math.min(22,amplitude*22)),x=(width-(count-1)*6)/2+i*6;g.strokeStyle=bar>2?'#909398':'#d1d3d6';g.beginPath();g.moveTo(x,height/2-bar/2);g.lineTo(x,height/2+bar/2+0.01);g.stroke();}
}
function drawWave(now=0){
 const values=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(values);let energy=0;for(const value of values)energy+=value*value;
 if(now-waveTick>=50){paintWave(Math.min(1,Math.sqrt(energy/values.length)*9),true);waveTick=now;}
 animation=requestAnimationFrame(drawWave);
}
async function ensureMic(){if(micStream)return;if(micPromise)return micPromise;micPromise=openMic();try{await micPromise;}finally{micPromise=null;}}
async function openMic(){
 if(micStream)return;
 micStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1},video:false});
 context=new AudioContext();await context.resume();source=context.createMediaStreamSource(micStream);analyser=context.createAnalyser();analyser.fftSize=1024;
 processor=context.createScriptProcessor(4096,1,1);silentGain=context.createGain();silentGain.gain.value=0;source.connect(analyser);source.connect(processor);processor.connect(silentGain);silentGain.connect(context.destination);
 processor.onaudioprocess=event=>{
  const samples=event.inputBuffer.getChannelData(0);let energy=0;for(const s of samples)energy+=s*s;const loud=Math.sqrt(energy/samples.length)>0.012,now=performance.now();
  if(state.wakeEnabled&&!recording&&!transcribing&&(audio||speechSynthesis.speaking||interruptActive)){
   const voice=Math.sqrt(energy/samples.length)>0.025;
   if(voice){if(!interruptStart)interruptStart=now;interruptLast=now;}
   if(interruptStart)interruptParts.push(new Float32Array(samples));
   if(!interruptActive&&interruptStart&&now-interruptLast>180){interruptParts=[];interruptStart=0;}
   if(!interruptActive&&interruptStart&&now-interruptStart>=220&&voice){interruptActive=true;stop();$('status').textContent='네, 말씀하세요. 듣고 있어요.';}
   if(interruptActive&&(now-interruptLast>900||now-interruptStart>15000)){
    const parts=interruptParts,rate=context.sampleRate;interruptParts=[];interruptStart=0;interruptActive=false;finishInterrupt(parts,rate);
   }
   return;
  }
  if(!interruptActive){interruptParts=[];interruptStart=0;}
  if(recording&&commandAuto){if(loud){commandSpoke=true;lastCommandLoud=now;}if(commandSpoke&&now-lastCommandLoud>1000)finishRecording();else if(!commandSpoke&&now-commandStarted>8000)finishRecording(true);return;}
  if(recording||transcribing||busy||wakeProcessing||speechSynthesis.speaking||audio||!state.wakeEnabled){resetWake();return;}
  const copy=new Float32Array(samples);preRoll.push(copy);if(preRoll.length>4)preRoll.shift();
  if(loud){if(!speechStart){speechStart=now;wakeParts=preRoll.slice(0,-1);}lastLoud=now;}
  if(speechStart)wakeParts.push(copy);
  if(speechStart&&((now-lastLoud>450)||(now-speechStart>3500))){const parts=wakeParts,rate=context.sampleRate,duration=now-speechStart;resetWake();if(duration>300)detectWake(parts,rate);}
 };
 $('mic-state').textContent=state.wakeEnabled?'마이크 켜짐 · 자비스 호출 대기':'마이크 켜짐 · 녹음';drawWave();
}
function closeMic(){interruptParts=[];interruptStart=0;interruptActive=false;clearTimeout(recordTimer);if(animation)cancelAnimationFrame(animation);if(processor)processor.disconnect();if(source)source.disconnect();if(silentGain)silentGain.disconnect();if(micStream)micStream.getTracks().forEach(t=>t.stop());if(context)context.close();micStream=null;context=null;processor=null;resetWake();$('mic-state').textContent='마이크 꺼짐';waveHistory=[];paintWave();}
async function syncWake(){ $('wake').checked=!!state.wakeEnabled;if(state.wakeEnabled){try{await ensureMic();if(!state.wakeEnabled&&!recording&&!recordStarting){closeMic();return;}$('mic-state').textContent='마이크 켜짐 · 자비스 호출 대기';}catch{$('mic-state').textContent='마이크 연결 필요';$('status').textContent='마이크 권한을 확인한 뒤 호출 듣기를 다시 켜주세요.';}}else if(!recording&&!recordStarting)closeMic(); }
const wakeReplies=['네, 말씀하세요.','네, 무엇을 도와드릴까요?','네, 듣고 있어요.'];
let replyIndex=0,warmedVoice='';
async function warmWakeReplies(){
 if(!state.ttsUrl||state.paused||warmedVoice===state.ttsUrl)return;
 warmedVoice=state.ttsUrl;
 for(const text of wakeReplies){try{await window.jarvis.tts(text);}catch{warmedVoice='';break;}}
}
async function acknowledgeWake(){
 const text=wakeReplies[replyIndex++%wakeReplies.length];
 $('status').textContent=text;message(text,'assistant');
 busy=true;
 try{await speak(text);}finally{busy=false;}
}
async function detectWake(parts,rate){wakeProcessing=true;try{
 const result=await window.jarvis.transcribe(VoiceUtils.wav(parts,rate),'wake');if(!state.wakeEnabled||recording||busy)return;
 const tail=VoiceUtils.wakeCommand(result.text||'');if(tail===null)return;
 await window.jarvis.show();$('status').textContent='네, 듣고 있어요.';
 if(tail.length>1){$('input').value=tail;await send(tail);}else {await acknowledgeWake();if(state.wakeEnabled&&!recording&&!recordStarting&&!interruptActive&&!transcribing)await startRecording(true);}
 }catch{}finally{wakeProcessing=false;}}
function finishRecording(cancel=false){cancelRecording=cancel;clearTimeout(recordTimer);if(recorder&&recorder.state==='recording')recorder.stop();}
async function startRecording(auto=false){
 if(recording){finishRecording();return;}if(busy||transcribing||recordStarting)return;
 stop();cancelRecording=false;recordStarting=true;commandAuto=auto;commandSpoke=false;commandStarted=performance.now();recordingControls();
 try{
  await ensureMic();if(cancelRecording){if(!state.wakeEnabled)closeMic();return;}
  const parts=[];recorder=new MediaRecorder(micStream,{mimeType:'audio/webm;codecs=opus'});
  recorder.ondataavailable=event=>{if(event.data.size)parts.push(event.data);};
  recorder.onerror=()=>{finishRecording(true);$('status').textContent='녹음하지 못했어요. 마이크를 확인해주세요.';};
  recorder.onstop=async()=>{
   recording=false;recorder=null;clearTimeout(recordTimer);if(!state.wakeEnabled)closeMic();
   if(cancelRecording){recordingControls();$('status').textContent='녹음을 취소했어요.';return;}
   transcribing=true;recordingControls();$('status').textContent='음성을 글로 바꾸고 있어요…';let autoText='';
   try{
    const result=await window.jarvis.transcribe(new Uint8Array(await new Blob(parts,{type:'audio/webm'}).arrayBuffer()));if(result.error)throw Error(result.error);
    if(!result.text?.trim()){$('status').textContent='말소리를 찾지 못했어요. 다시 말씀해주세요.';return;}
    if(result.text.length>250){$('status').textContent='내용이 길어요. 더 짧게 말씀해주세요.';return;}
    $('input').value=auto?result.text.trim():[$('input').value.trim(),result.text.trim()].filter(Boolean).join(' ');
    $('status').textContent=`인식 완료 (${result.seconds||0}초). 내용을 확인하고 보내주세요.`;$('input').focus();if(auto)autoText=result.text.trim();
   }catch(error){$('status').textContent=error.message||'음성을 변환하지 못했어요.';}
   finally{transcribing=false;recordingControls();resetWake();}
   if(autoText)await send(autoText);
  };
  recorder.start();recording=true;$('status').textContent=auto?'듣고 있어요. 말을 마치면 자동으로 처리해요.':'듣고 있어요. 다 말했으면 녹음 끝내기를 눌러주세요.';recordTimer=setTimeout(()=>finishRecording(),60000);
 }catch{$('status').textContent='마이크를 사용할 수 없어요. 연결과 접근 권한을 확인해주세요.';if(!state.wakeEnabled)closeMic();recording=false;}
 finally{recordStarting=false;recordingControls();}
}
$('dictate').onclick=()=>startRecording(false);
$('wake').onchange=async()=>{update(await window.jarvis.wake($('wake').checked));await syncWake();};
window.jarvis.onState(()=>syncWake());window.jarvis.state().then(value=>{update(value);syncWake();});
window.jarvis.onStopRecording(()=>{if(recording||recordStarting)finishRecording(true);if(!state.wakeEnabled)closeMic();});
window.addEventListener('beforeunload',()=>{finishRecording(true);closeMic();});

$('record-cancel').onclick=()=>finishRecording(true);
$('record-stop').onclick=()=>finishRecording();
window.addEventListener('resize',()=>paintWave());
recordingControls();paintWave();

window.jarvis.onState(()=>warmWakeReplies());window.jarvis.state().then(value=>{update(value);warmWakeReplies();});

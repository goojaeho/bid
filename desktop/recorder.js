let micPromise=null;
let recorder=null,micStream=null,recordTimer=null,recordStarting=false,cancelRecording=false;
let context=null,analyser=null,processor=null,source=null,silentGain=null,animation=null;
let wakeProcessing=false,wakeParts=[],preRoll=[],speechStart=0,lastLoud=0,commandAuto=false,commandSpoke=false,commandStarted=0,lastCommandLoud=0;
function recordingControls(){ $('dictate').textContent=transcribing?'글로 바꾸는 중…':recording?'■ 녹음 끝내기':'마이크';$('dictate').disabled=transcribing||recordStarting;$('send').disabled=recording||transcribing;$('brief').disabled=recording||transcribing; }
function resetWake(){wakeParts=[];preRoll=[];speechStart=0;lastLoud=0;}
function drawWave(){
 const canvas=$('waveform'),g=canvas.getContext('2d');g.clearRect(0,0,canvas.width,canvas.height);
 const values=new Uint8Array(analyser.fftSize);analyser.getByteTimeDomainData(values);g.lineWidth=2;g.strokeStyle=recording?'#c4a9ff':'#adb5ca';g.beginPath();
 for(let i=0;i<values.length;i++){const x=i/(values.length-1)*canvas.width,y=(values[i]-128)*0.9+canvas.height/2;i?g.lineTo(x,y):g.moveTo(x,y);}g.stroke();animation=requestAnimationFrame(drawWave);
}
async function ensureMic(){if(micStream)return;if(micPromise)return micPromise;micPromise=openMic();try{await micPromise;}finally{micPromise=null;}}
async function openMic(){
 if(micStream)return;
 micStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1},video:false});
 context=new AudioContext();await context.resume();source=context.createMediaStreamSource(micStream);analyser=context.createAnalyser();analyser.fftSize=1024;
 processor=context.createScriptProcessor(4096,1,1);silentGain=context.createGain();silentGain.gain.value=0;source.connect(analyser);source.connect(processor);processor.connect(silentGain);silentGain.connect(context.destination);
 processor.onaudioprocess=event=>{
  const samples=event.inputBuffer.getChannelData(0);let energy=0;for(const s of samples)energy+=s*s;const loud=Math.sqrt(energy/samples.length)>0.012,now=performance.now();
  if(recording&&commandAuto){if(loud){commandSpoke=true;lastCommandLoud=now;}if(commandSpoke&&now-lastCommandLoud>1000)finishRecording();else if(!commandSpoke&&now-commandStarted>8000)finishRecording(true);return;}
  if(recording||transcribing||busy||wakeProcessing||speechSynthesis.speaking||audio||!state.wakeEnabled){resetWake();return;}
  const copy=new Float32Array(samples);preRoll.push(copy);if(preRoll.length>4)preRoll.shift();
  if(loud){if(!speechStart){speechStart=now;wakeParts=preRoll.slice(0,-1);}lastLoud=now;}
  if(speechStart)wakeParts.push(copy);
  if(speechStart&&((now-lastLoud>450)||(now-speechStart>3500))){const parts=wakeParts,rate=context.sampleRate,duration=now-speechStart;resetWake();if(duration>300)detectWake(parts,rate);}
 };
 $('mic-state').textContent=state.wakeEnabled?'마이크 켜짐 · 자비스 호출 대기':'마이크 켜짐 · 녹음';drawWave();
}
function closeMic(){clearTimeout(recordTimer);if(animation)cancelAnimationFrame(animation);if(processor)processor.disconnect();if(source)source.disconnect();if(silentGain)silentGain.disconnect();if(micStream)micStream.getTracks().forEach(t=>t.stop());if(context)context.close();micStream=null;context=null;processor=null;resetWake();$('mic-state').textContent='마이크 꺼짐';const c=$('waveform');c.getContext('2d').clearRect(0,0,c.width,c.height);}
async function syncWake(){ $('wake').checked=!!state.wakeEnabled;if(state.wakeEnabled){try{await ensureMic();if(!state.wakeEnabled&&!recording&&!recordStarting){closeMic();return;}$('mic-state').textContent='마이크 켜짐 · 자비스 호출 대기';}catch{$('mic-state').textContent='마이크 연결 필요';$('status').textContent='마이크 권한을 확인한 뒤 호출 듣기를 다시 켜주세요.';}}else if(!recording&&!recordStarting)closeMic(); }
async function detectWake(parts,rate){wakeProcessing=true;try{
 const result=await window.jarvis.transcribe(VoiceUtils.wav(parts,rate),'wake');if(!state.wakeEnabled||recording||busy)return;
 const tail=VoiceUtils.wakeCommand(result.text||'');if(tail===null)return;
 await window.jarvis.show();$('status').textContent='네, 듣고 있어요.';
 if(tail.length>1){$('input').value=tail;await send(tail);}else await startRecording(true);
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

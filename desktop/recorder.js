let recorder=null,micStream=null,recordTimer=null,recordStarting=false,cancelRecording=false;
function releaseMic(){clearTimeout(recordTimer);if(micStream){micStream.getTracks().forEach(track=>track.stop());micStream=null;}}
function recordingControls(){ $('dictate').textContent=transcribing?'글로 바꾸는 중…':recording?'■ 녹음 끝내기':'마이크';$('dictate').disabled=transcribing||recordStarting;$('send').disabled=recording||transcribing;$('brief').disabled=recording||transcribing; }
function finishRecording(cancel=false){cancelRecording=cancel;if(recorder&&recorder.state==='recording')recorder.stop();releaseMic();}
$('dictate').onclick=async()=>{
 if(recording){finishRecording();return;}
 if(busy||transcribing||recordStarting)return;
 stop();cancelRecording=false;recordStarting=true;recordingControls();
 try{
  micStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1},video:false});
  if(cancelRecording){releaseMic();return;}
  const parts=[];recorder=new MediaRecorder(micStream,{mimeType:'audio/webm;codecs=opus'});
  recorder.ondataavailable=event=>{if(event.data.size)parts.push(event.data);};
  recorder.onerror=()=>{finishRecording(true);$('status').textContent='녹음하지 못했어요. 마이크를 확인해주세요.';};
  recorder.onstop=async()=>{
   releaseMic();recording=false;recorder=null;
   if(cancelRecording){recordingControls();$('status').textContent='녹음을 취소했어요.';return;}
   transcribing=true;recordingControls();$('status').textContent='이 컴퓨터에서 음성을 글로 바꾸고 있어요…';
   try{
    const bytes=new Uint8Array(await new Blob(parts,{type:'audio/webm'}).arrayBuffer());
    const result=await window.jarvis.transcribe(bytes);
    if(result.error)throw Error(result.error);
    if(!result.text?.trim()){$('status').textContent='말소리를 찾지 못했어요. 다시 녹음해주세요.';return;}
    if(result.text.length>250){$('status').textContent='인식 내용이 250자를 넘었어요. 더 짧게 말씀해주세요.';return;}
    $('input').value=[$('input').value.trim(),result.text.trim()].filter(Boolean).join(' ');
    if($('input').value.length>250){$('status').textContent='입력 내용이 길어요. 250자 이내로 수정한 뒤 보내주세요.';}else $('status').textContent='내용을 확인하고 보내기를 눌러주세요.';
    $('input').focus();
   }catch(error){$('status').textContent=error.message||'음성을 변환하지 못했어요.';}
   finally{transcribing=false;recordingControls();}
  };
  recorder.start();recording=true;$('status').textContent='듣고 있어요. 다 말했으면 녹음 끝내기를 눌러주세요. (최대 60초)';recordTimer=setTimeout(()=>finishRecording(),60000);
 }catch{$('status').textContent='마이크를 사용할 수 없어요. Windows 설정에서 마이크 연결과 앱 접근 권한을 확인해주세요.';releaseMic();recording=false;}
 finally{recordStarting=false;recordingControls();}
};
window.jarvis.onStopRecording(()=>{if(recording||recordStarting)finishRecording(true);});
window.addEventListener('beforeunload',()=>finishRecording(true));

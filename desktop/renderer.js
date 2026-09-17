const $=id=>document.getElementById(id);
let state={},busy=false,audio=null,generation=0,recording=false,transcribing=false,playbackResolve=null;
function stop(){generation++;if(playbackResolve){playbackResolve();playbackResolve=null;}speechSynthesis.cancel();if(audio){audio.pause();URL.revokeObjectURL(audio.src);audio=null;}}
function update(value){const wasPaused=state.paused;if(state.connected!==value.connected)document.querySelector('details').open=!value.connected;state=value;$('connection').textContent=value.connected?'로그인됨':'로그인 필요';$('pause').textContent=value.paused?'안내 재개':'안내 멈춤';if(value.paused&&!wasPaused)stop();}
function message(text,role){const node=document.createElement('article');node.className=role;node.textContent=text;$('conversation').append(node);node.scrollIntoView();}
function speechChunks(text){const chunks=[];for(const sentence of text.split(/(?<=[.!?])\s+/)){let rest=sentence;while(rest.length>400){chunks.push(rest.slice(0,400));rest=rest.slice(400);}if(rest)chunks.push(rest);}return chunks;}
async function speak(text){
 stop();if(state.paused)return;const current=generation;
 if(state.ttsUrl){
  try{
   const chunks=speechChunks(text);
   let next=window.jarvis.tts(chunks[0]);
   for(let i=0;i<chunks.length;i++){
    const result=await next;if(current!==generation||state.paused)return;
    if(!result)throw Error();
    next=i+1<chunks.length?window.jarvis.tts(chunks[i+1]).catch(()=>null):null;
    const bytes=Uint8Array.from(atob(result.data),c=>c.charCodeAt(0));
    audio=new Audio(URL.createObjectURL(new Blob([bytes],{type:result.mime})));
    const playing=audio;
    await new Promise((resolve,reject)=>{playbackResolve=resolve;playing.onended=resolve;playing.onerror=reject;playing.play().catch(reject);});
    playbackResolve=null;URL.revokeObjectURL(playing.src);if(audio===playing)audio=null;
    if(current!==generation||state.paused)return;
   }
   return;
  }catch{$('status').textContent='맥 음성 서버 연결을 확인해주세요. 이번에는 기본 음성으로 읽을게요.';}
 }
 if(current!==generation||state.paused)return;
 const utterance=new SpeechSynthesisUtterance(text);utterance.lang='ko-KR';const voices=speechSynthesis.getVoices().filter(v=>v.lang.toLowerCase().startsWith('ko'));utterance.voice=voices.find(v=>/Heami|SunHi|female/i.test(v.name))||voices[0]||null;utterance.rate=1;speechSynthesis.speak(utterance);
}
async function send(text){if(busy||recording||transcribing||!text.trim())return;busy=true;$('send').disabled=true;message(text,'user');$('input').value='';$('status').textContent='처리 중이에요…';try{const result=await window.jarvis.command(text);message(result.message,'assistant');$('status').textContent=result.ok?'완료했어요.':'내용을 확인해주세요.';await speak(result.message);}catch{message('처리 결과를 확인하지 못했어요. 기존 사이트에서 등록 여부를 확인해주세요.','assistant');}finally{busy=false;$('send').disabled=false;}}
$('form').onsubmit=e=>{e.preventDefault();send($('input').value);};$('brief').onclick=()=>send('브리핑해봐');
$('pause').onclick=()=>window.jarvis.pause(!state.paused);
$('site').onclick=()=>window.jarvis.site();
$('login').onclick=async()=>{try{await window.jarvis.login();$('status').textContent='브라우저에서 Google 로그인을 완료해주세요.';}catch{$('status').textContent='로그인을 시작하지 못했어요. 인터넷 연결을 확인하고 다시 시도해주세요.';}};
$('logout').onclick=()=>{stop();window.jarvis.logout();};
$('save').onclick=async()=>{try{update(await window.jarvis.settings({ttsUrl:$('tts').value,autoStart:$('autostart').checked}));$('status').textContent='설정을 저장했어요.';}catch{$('status').textContent='설정 주소를 확인해주세요.';}};
window.jarvis.onState(update);window.jarvis.onNotice(text=>$('status').textContent=text);
window.jarvis.state().then(value=>{update(value);$('tts').value=value.ttsUrl;$('autostart').checked=value.autoStart;});

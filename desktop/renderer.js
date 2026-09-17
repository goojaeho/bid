const $=id=>document.getElementById(id);
let state={},busy=false,audio=null,generation=0;
function stop(){generation++;speechSynthesis.cancel();if(audio){audio.pause();URL.revokeObjectURL(audio.src);audio=null;}}
function update(value){const wasPaused=state.paused;state=value;$('connection').textContent=value.connected?'로그인됨':'로그인 필요';$('pause').textContent=value.paused?'안내 재개':'안내 멈춤';if(value.paused&&!wasPaused)stop();}
function message(text,role){const node=document.createElement('article');node.className=role;node.textContent=text;$('conversation').append(node);node.scrollIntoView();}
async function speak(text){stop();if(state.paused)return;const current=generation;
  if(state.ttsUrl){try{const result=await window.jarvis.tts(text);if(current!==generation||state.paused)return;if(result){const bytes=Uint8Array.from(atob(result.data),c=>c.charCodeAt(0));audio=new Audio(URL.createObjectURL(new Blob([bytes],{type:result.mime})));audio.onended=()=>{if(current===generation)stop();};await audio.play();return;}}catch{$('status').textContent='맥 음성 서버에 연결하지 못해 기본 음성으로 읽을게요.';}}
  if(current!==generation||state.paused)return;const utterance=new SpeechSynthesisUtterance(text);utterance.lang='ko-KR';const voices=speechSynthesis.getVoices().filter(v=>v.lang.toLowerCase().startsWith('ko'));utterance.voice=voices.find(v=>/Heami|SunHi|female/i.test(v.name))||voices[0]||null;utterance.rate=1;speechSynthesis.speak(utterance);
}
async function send(text){if(busy||!text.trim())return;busy=true;$('send').disabled=true;message(text,'user');$('input').value='';$('status').textContent='처리 중이에요…';try{const result=await window.jarvis.command(text);message(result.message,'assistant');$('status').textContent=result.ok?'완료했어요.':'내용을 확인해주세요.';await speak(result.message);}catch{message('처리 결과를 확인하지 못했어요. 기존 사이트에서 등록 여부를 확인해주세요.','assistant');}finally{busy=false;$('send').disabled=false;}}
$('form').onsubmit=e=>{e.preventDefault();send($('input').value);};$('brief').onclick=()=>send('브리핑해봐');
$('pause').onclick=()=>window.jarvis.pause(!state.paused);
$('dictate').onclick=()=>{$('input').focus();$('status').textContent='지금 Win+H를 누르고 말한 뒤, Enter로 보내세요. 마이크를 계속 듣는 기능은 아직 켜지지 않아요.';};
$('site').onclick=()=>window.jarvis.site();
$('login').onclick=async()=>{try{await window.jarvis.login();$('status').textContent='브라우저에서 Google 로그인을 완료해주세요.';}catch{$('status').textContent='로그인을 시작하지 못했어요. 인터넷 연결을 확인하고 다시 시도해주세요.';}};
$('logout').onclick=()=>{stop();window.jarvis.logout();};
$('save').onclick=async()=>{try{update(await window.jarvis.settings({ttsUrl:$('tts').value,autoStart:$('autostart').checked}));$('status').textContent='설정을 저장했어요.';}catch{$('status').textContent='설정 주소를 확인해주세요.';}};
window.jarvis.onState(update);window.jarvis.onNotice(text=>$('status').textContent=text);
window.jarvis.state().then(value=>{update(value);$('tts').value=value.ttsUrl;$('autostart').checked=value.autoStart;});

CONTENT = r'''
<div class="card">
  <h2>자비스 첫 시험</h2>
  <p>할 일을 말로 등록하고 기존 사이트의 할 일을 브리핑합니다.</p>
  <p class="meta">예: “자비스, 내일 보고서 작성 할 일 등록해줘”, “브리핑해봐”</p>
  <p class="meta">마이크 버튼을 눌러 말해주세요. 창을 닫은 상태의 호출, 약속 시간 충돌 확인, 캘린더·메일 연동은 아직 지원하지 않습니다. 음성 인식은 브라우저 제공 서비스로 처리될 수 있습니다.</p>
  <form id="jarvis-form" class="row">
    <input id="jarvis-input" aria-label="자비스에게 할 말" maxlength="250" placeholder="내일 보고서 작성 할 일 등록해줘" required>
    <button id="jarvis-send">보내기</button>
  </form>
  <div class="row">
    <button type="button" id="jarvis-mic">말하기</button>
    <button type="button" id="jarvis-brief">브리핑해봐</button>
    <button type="button" id="jarvis-stop">음성 중지</button>
    <a href="/todo" target="_blank" rel="noopener">기존 할 일 확인</a>
  </div>
  <p id="jarvis-status" role="status" aria-live="polite">준비됐어요.</p>
  <p id="jarvis-result" style="white-space:pre-wrap"></p>
</div>
<script>
(() => {
  const input = document.getElementById('jarvis-input');
  const status = document.getElementById('jarvis-status');
  const result = document.getElementById('jarvis-result');
  const mic = document.getElementById('jarvis-mic');
  const speech = window.speechSynthesis;
  let busy = false, listening = false;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const recognition = Recognition ? new Recognition() : null;
  function stop() { if (speech) speech.cancel(); }
  async function send(text) {
    if (busy || !text.trim()) return;
    stop();
    if (recognition && listening) recognition.abort();
    busy = true;
    status.textContent = '처리 중…';
    try {
      const response = await fetch('/api/jarvis/command', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})
      });
      const data = await response.json();
      result.textContent = data.message || '처리하지 못했습니다.';
      status.textContent = data.ok ? '완료' : '확인이 필요해요.';
      if (data.ok && data.kind === 'created') input.value = '';
      if (speech) { const utterance = new SpeechSynthesisUtterance(result.textContent); utterance.lang='ko-KR'; speech.speak(utterance); }
    } catch (_) {
      status.textContent = '응답을 확인하지 못했습니다. 중복 등록을 피하려면 할 일 화면을 먼저 확인해주세요.';
    } finally { busy = false; }
  }
  document.getElementById('jarvis-form').onsubmit = e => { e.preventDefault(); send(input.value); };
  document.getElementById('jarvis-brief').onclick = () => send('브리핑해봐');
  document.getElementById('jarvis-stop').onclick = stop;
  if (recognition) {
    recognition.lang='ko-KR'; recognition.interimResults=false; recognition.maxAlternatives=1;
    recognition.onresult = event => {
      const text = event.results[0][0].transcript;
      input.value = text;
      send(text);
    };
    recognition.onerror = () => { status.textContent='음성을 인식하지 못했습니다. 마이크 권한을 확인하거나 글로 입력해주세요.'; };
    recognition.onend = () => { listening=false; mic.textContent='말하기'; };
    mic.onclick = () => {
      if (busy) return;
      if (listening) { recognition.abort(); return; }
      stop();
      try { recognition.start(); listening=true; mic.textContent='듣기 중지'; status.textContent='듣고 있어요…'; }
      catch (_) { status.textContent='마이크를 시작할 수 없습니다.'; }
    };
  } else { mic.disabled=true; status.textContent='이 브라우저는 음성 인식을 지원하지 않습니다. 글로 입력할 수 있어요.'; }
  window.addEventListener('pagehide', () => { stop(); if (recognition) recognition.abort(); });
})();
</script>
'''

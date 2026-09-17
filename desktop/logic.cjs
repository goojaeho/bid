function localCommand(text) {
  const t = text.replace(/^자비스[야,\s]*/, '').trim();
  if (/^(안내\s*)?(잠깐\s*)?(멈춰|중지해)[.!?]?$/.test(t) || /회의.*(멈춰|중지)/.test(t)) return 'pause';
  if (/^(안내\s*)?(다시\s*)?(시작해|재개해)[.!?]?$/.test(t)) return 'resume';
  return null;
}
function validateTts(value) {
  if (!value) return '';
  const u = new URL(value);
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.hash) throw new Error('HTTP 또는 HTTPS 음성 주소를 입력해주세요.');
  return u.href;
}
module.exports = { localCommand, validateTts };

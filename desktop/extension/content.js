chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==chrome.runtime.id||message.type!=='jarvis-command'||typeof message.text!=='string'||message.text.length>250)return;
 fetch('/api/jarvis/command',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:message.text}),signal:AbortSignal.timeout(22000)})
 .then(async response=>{if(response.status===403)return {ok:false,message:'기존 사이트에 소유자 계정으로 로그인해주세요.'};return response.json();})
 .then(respond).catch(()=>respond({ok:false,message:'응답을 확인하지 못했어요. 사이트에서 등록 여부를 먼저 확인해주세요.'}));
 return true;
});

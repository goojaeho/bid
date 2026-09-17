let socket=null,timer=null,retry=null;
async function siteTab(){const tabs=await chrome.tabs.query({url:'https://www.oneaigen.com/*'});return tabs.find(t=>t.url.startsWith('https://www.oneaigen.com/jarvis'))||tabs[0];}
async function connect(){
 clearTimeout(retry);clearInterval(timer);if(socket){socket.onclose=null;socket.close();}
 const {pairing}=await chrome.storage.local.get('pairing');if(!pairing)return;
 const ws=new WebSocket('ws://127.0.0.1:17843');socket=ws;
 ws.onopen=()=>{ws.send(JSON.stringify({type:'pair',token:pairing}));const status=async()=>{const tab=await siteTab();if(ws.readyState===1)ws.send(JSON.stringify({type:'status',ready:!!tab}));};status();timer=setInterval(status,15000);};
 ws.onmessage=async event=>{let data;try{data=JSON.parse(event.data);}catch{return;}
   if(data.type!=='command'||typeof data.id!=='string'||typeof data.text!=='string'||data.text.length>250)return;
   let result;try{const tab=await siteTab();if(!tab)throw Error();result=await chrome.tabs.sendMessage(tab.id,{type:'jarvis-command',text:data.text});}catch{result={ok:false,message:'사이트 탭을 새로고침하고 로그인 상태를 확인해주세요.'};}
   if(ws.readyState===1)ws.send(JSON.stringify({type:'result',id:data.id,result}));
 };
 ws.onclose=()=>{clearInterval(timer);retry=setTimeout(connect,5000);};ws.onerror=()=>ws.close();
}
chrome.runtime.onMessage.addListener((msg,_sender,reply)=>{if(msg.type==='reconnect'){connect();reply({ok:true});}if(msg.type==='status')reply({connected:socket?.readyState===1});});
chrome.runtime.onStartup.addListener(connect);chrome.runtime.onInstalled.addListener(connect);connect();

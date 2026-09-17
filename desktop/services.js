let mailFeed={items:[],pending:[],error:''},lastSummary=[];
function button(label,handler){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=handler;return b;}
function showResult(result){
 message(result.message||'완료했어요.','assistant');
 const box=$('conversation').lastElementChild;
 if(result.kind==='calendar-proposal'||result.kind==='proposal')box.append(button('승인',()=>perform(()=>window.jarvis.approve(result.proposalId))));
 for(const source of result.sources||[])box.append(button(`[${source.source}:${source.id}] ${source.title}`,()=>window.jarvis.openLink(source.url)));
 for(const event of result.events||[])box.append(button(event.summary+' 수정…',()=>{const start=event.start.dateTime?new Date(event.start.dateTime):null;const date=start?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(start):event.start.date;const time=start?new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit'}).format(start):'';$('event-title').value=event.summary;$('event-date').value=date;$('event-time').value=time;$('event-id').value=event.id;if(start)$('event-duration').value=Math.round((Date.parse(event.end.dateTime)-start.getTime())/60000);$('event-title').closest('details').open=true;$('event-title').scrollIntoView();}));
 if(result.event?.htmlLink)box.append(button('Google 캘린더에서 확인',()=>window.jarvis.openLink(result.event.htmlLink)));
 if(result.coverage?.length)message('일부 기록을 불러오지 못했어요: '+result.coverage.join(', '),'assistant');
}
async function perform(operation){
 if(busy||recording||transcribing)return;
 busy=true;try{const r=await operation();showResult(r);await speak(r.message||'완료했어요.');}catch{message('요청을 처리하지 못했어요. 연결 상태를 확인해주세요.','assistant');}finally{busy=false;}
}
function updateMail(feed){
 mailFeed=feed;$('mail-status').textContent=feed.error||`최근 저장 메일 ${feed.items.length}개 · 안내 대기 ${feed.pending.length}개`;
 $('mail-list').replaceChildren();
 for(const mail of feed.items){
  const item=document.createElement('article');const title=document.createElement('p');title.textContent=(mail.category==='promo'?'[광고] ':'')+mail.subject;item.append(title);
  item.append(button('읽기',()=>perform(async()=>{const r=await window.jarvis.mailAction('read',mail.id);return r.ok?{ok:true,message:r.item.subject+'\n'+(r.item.body||r.item.summary||r.item.snippet).slice(0,2500)}:r;})));
  item.append(button('일정 제안',()=>perform(()=>window.jarvis.mailAction('event',mail.id))));
  item.append(button('휴지통 이동…',async()=>{const r=await window.jarvis.mailAction('trash-preview',mail.id);const label=document.createElement('p');label.textContent=r.message;item.append(label);if(r.ok)item.append(button('이 메일 휴지통으로 이동',()=>perform(()=>window.jarvis.mailAction('trash',mail.id))));}));
  $('mail-list').append(item);
 }
}
window.jarvis.onMail(updateMail);
$('mail-refresh').onclick=async()=>{updateMail(await window.jarvis.mailRefresh());};
$('google-connect').onclick=()=>window.jarvis.googleConnect();
$('calendar-list').onclick=()=>perform(()=>window.jarvis.calendarList());
$('event-new').onclick=()=>{$('event-id').value='';$('event-title').value='';$('event-time').value='';};
$('event-preview').onclick=()=>perform(()=>window.jarvis.calendarPreview({title:$('event-title').value,date:$('event-date').value,time:$('event-time').value,duration_min:Number($('event-duration').value)},$('event-id').value));
$('record-search').onclick=async()=>{
 $('record-results').textContent='메일·회의록을 검색하고 있어요…';
 try{const r=await window.jarvis.search($('record-query').value);$('record-results').textContent=`저장 기록 ${r.total}개 중 ${r.matches}개 일치 (상위 8개 표시)`;
 for(const item of r.items){const p=document.createElement('p');p.textContent=`[${item.source}:${item.id}] ${item.title}\n${item.text}`;$('record-results').append(p,button('사이트에서 보기',()=>window.jarvis.openLink(item.url)));}
 if(r.errors.length)$('record-results').append(document.createTextNode('일부 조회 실패: '+r.errors.join(', ')));
 }catch{$('record-results').textContent='검색하지 못했어요. 사이트 연결을 확인해주세요.';}
};
$('mail-read-queue').onclick=()=>perform(async()=>{const items=mailFeed.pending.length?mailFeed.pending:lastSummary;return {ok:true,message:items.length?items.map((x,i)=>`${i+1}번째, ${x.sender}. ${x.subject}. ${x.summary||''}`).join('\n'):'밀린 메일이 없어요.'};});
setInterval(async()=>{
 if(state.paused||busy||recording||transcribing||!mailFeed.pending.length)return;
 const items=mailFeed.pending.slice();lastSummary=items;busy=true;
 let text=items.length===1?`새 메일이에요. ${items[0].sender}. ${items[0].subject}. ${items[0].summary||''} 일정이 있으면 메일의 일정 제안 버튼으로 확인해 주세요.`:`밀린 메일이 ${items.length}개 있어요. ${items.slice(0,3).map(x=>x.subject).join('. ')}. 하나씩 읽기를 누르면 자세히 안내할게요.`;
 try{if(items.length===1&&/회의|미팅|약속|일정|[0-9]+시/.test(items[0].subject+' '+items[0].summary)){const proposal=await window.jarvis.mailAction('event-auto',items[0].id);if(proposal.kind==='calendar-proposal'){showResult(proposal);text+=' '+proposal.message;}}message(text,'assistant');await speak(text);if(!state.paused){await window.jarvis.mailAck(items.map(x=>x.id));mailFeed.pending=mailFeed.pending.filter(x=>!items.some(y=>y.id===x.id));}}finally{busy=false;}
},2000);

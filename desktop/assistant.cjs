// The model proposes an interpretation; only this controller can execute it.
const {randomUUID}=require('node:crypto');
function calendarLine(event){const start=event.start||{};return (start.dateTime?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'long',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(start.dateTime)):start.date+' 종일')+' '+event.summary;}
function todayKst(){return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());}
function canonicalTodo(intent,today=todayKst()){
 const title=intent.title;
 if(typeof title!=='string'||!title.trim()||title.length>160||/[\r\n]/.test(title))throw Error('할 일 내용을 다시 말씀해주세요.');
 const due=intent.due_date||null;
 let prefix='';
 if(due){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(due)||Number.isNaN(Date.parse(due))||new Date(due).toISOString().slice(0,10)!==due)throw Error('날짜를 다시 확인해주세요.');
  // Existing website accepts month/day and rolls past dates to next year.
  const expected=Number(today.slice(0,4))+(due.slice(5)<today.slice(5)?1:0);
  if(Number(due.slice(0,4))!==expected)throw Error('이 날짜는 현재 등록 기능으로 처리할 수 없어요. 기존 사이트에서 입력해주세요.');
  prefix=Number(due.slice(5,7))+'월 '+Number(due.slice(8,10))+'일 ';
 }
 if(/오늘|내일|모레|요일|\d+\s*[월/]|등록해|추가해/.test(title))throw Error('할 일 제목과 날짜를 나누어 다시 말씀해주세요.');
 return prefix+title.trim()+' 할 일 등록해줘';
}
class Assistant {
 constructor({infer,execute,service,records}){this.infer=infer;this.execute=execute;this.service=service;this.records=records;this.history=[];this.proposal=null;}
 reset(){this.history=[];this.proposal=null;}
 remember(text,result){this.history.push({role:'user',content:text},{role:'assistant',content:result.message});this.history=this.history.slice(-12);return result;}
 async prepareEvent(schedule,event_id=''){
  const r=await this.service('calendar.preview',{schedule,event_id});
  if(!r.ok)return r;
  if(r.conflicts.length){this.proposal=null;return {ok:false,message:'겹치는 일정이 있어요: '+r.conflicts.map(x=>x.summary).join(', ')+'. 시간을 바꿔주세요.',conflicts:r.conflicts};}
  this.proposal={kind:'calendar',token:r.token,schedule,proposalId:randomUUID()};
  return {ok:true,kind:'calendar-proposal',proposalId:this.proposal.proposalId,message:`${schedule.date} ${schedule.time||'종일'}, ${schedule.title}${schedule.time?' ('+(schedule.duration_min||60)+'분)':''}. 캘린더에 반영할까요?`};
 }
 async approve(expectedId){
  const p=this.proposal;if(!p||(expectedId&&p.proposalId!==expectedId))return {ok:false,message:'이 제안은 만료됐어요. 최신 제안을 확인해주세요.'};this.proposal=null;
  return p.kind==='calendar'?this.service('calendar.commit',{token:p.token}):this.execute(canonicalTodo(p));
 }
 async context(text){
  if(!this.service)return {};
  if(/잡아\s*줘|등록해\s*줘|추가해\s*줘/.test(text)&&!/메일|회의록|기록|그것|그거/.test(text))return {};
  const today=todayKst(),end=new Date(today+'T00:00:00+09:00');end.setDate(end.getDate()+30);
  const [records,cal]=await Promise.all([(/메일|회의록|기록|검색|찾아|내용|지난|그때/.test(text)?this.records.search(text):Promise.resolve({items:[],errors:[],truncated:false,matches:0})),this.service('calendar.list',{start:today+'T00:00:00+09:00',end:end.toISOString()}).catch(()=>({ok:false,message:'캘린더 조회 실패'}))]);
  return {records:{...records,items:records.items.slice(0,3).map(x=>({...x,text:x.text.slice(0,700)})),truncated:records.truncated||records.matches>3},calendar:{items:(cal.items||[]).slice(0,5).map(x=>({source:'calendar',id:x.id,title:x.summary,text:JSON.stringify({start:x.start,end:x.end}),url:x.htmlLink})),errors:cal.ok?[]:[cal.message],truncated:(cal.items||[]).length>5}};
 }
 async mailEvent(item){
  const result=await this.infer({messages:[{role:'user',content:'이 메일의 약속을 캘린더 등록 제안으로 만들어줘. 실행하지 마.'}],context:{today:todayKst(),timezone:'Asia/Seoul',records:{items:[{source:'mail',id:String(item.id),title:item.subject,text:JSON.stringify({subject:item.subject,body:(item.body||item.summary||'').slice(0,1800),schedule:item.schedule})}],errors:[],truncated:false}}});
  if(result.intent?.type==='add_event'){const prepared=await this.prepareEvent(result.intent.schedule);return {...prepared,schedule:result.intent.schedule};}
  return {ok:false,message:result.reply||'메일에 날짜와 시간이 명확하지 않아요. 직접 입력해주세요.'};
 }
 async command(text){
  const clean=text.trim().replace(/[.!?]+$/,'');
  if(this.records&&/메일|회의록/.test(clean)&&/찾아|검색/.test(clean)){
   const r=await this.records.search(clean);
   return this.remember(text,{ok:r.errors.length===0,message:`사이트 저장 기록 ${r.total}개 중 ${r.matches}개가 검색됐어요.\n`+r.items.slice(0,3).map(x=>`[${x.source}:${x.id}] ${x.title}\n${x.text.slice(0,500)}`).join('\n'),sources:r.items,coverage:r.errors});
  }
  if(this.proposal&&/^(응|네|예|좋아|등록해줘|넣어줘|그렇게 해|그래)$/.test(clean)){
   return this.remember(text,await this.approve());
  }
  if(this.proposal&&/^(아니|아니요|취소|취소해|됐어|넣지 마)$/.test(clean)){
   this.proposal=null;return this.remember(text,{ok:true,message:'네, 등록하지 않을게요.'});
  }
  if(!this.service&&/^(브리핑(\s*해봐|\s*해줘)?|오늘 할 일 알려줘)$/.test(clean))return this.remember(text,await this.execute('브리핑해봐'));
  const briefing=await this.execute('브리핑해봐');
  const context=await this.context(text);
  const result=await this.infer({messages:[...this.history.slice(-6).map(x=>({...x,content:x.content.slice(0,300)})),{role:'user',content:text}],context:{...context,today:todayKst(),timezone:'Asia/Seoul',briefing:briefing.ok?briefing.message.slice(0,700):'기존 할 일 조회 실패. 기록을 추측하지 마세요.',pending_todo:this.proposal?.kind==='calendar'?null:this.proposal}});
  const intent=result.intent;
  if(!intent||typeof result.reply!=='string'||result.reply.length>4000)throw Error('대화 해석 결과를 확인하지 못했어요.');
  if(intent.type==='add_event'&&this.service)return this.remember(text,await this.prepareEvent(intent.schedule));
  if(intent.type==='calendar_list'&&this.service){
   const start=intent.start||todayKst(),end=intent.end||start;
   if(!/^\d{4}-\d{2}-\d{2}$/.test(start)||!/^\d{4}-\d{2}-\d{2}$/.test(end))return {ok:false,message:'조회 날짜를 다시 알려주세요.'};
   const until=end>start?end:new Date(Date.parse(start)+86400000).toISOString().slice(0,10);
   const r=await this.service('calendar.list',{start:start+'T00:00:00+09:00',end:until+'T00:00:00+09:00'});
   return this.remember(text,{...r,events:r.items,message:r.ok?(r.items.length?r.items.map(calendarLine).join('\n'):'이 기간의 기본 캘린더 일정은 없어요.'):r.message});
  }
  if(intent.type==='add_todo'){
   canonicalTodo(intent);
   this.proposal={title:intent.title,due_date:intent.due_date||null,proposalId:randomUUID()};
   // Explicitness is checked against this user turn, not trusted from model output.
   if(/(?:등록|추가|넣어)\s*해?\s*줘/.test(clean)&&!/(?:말고|하지\s*마|넣지|않|까요|방법)/.test(clean)){
    const proposal=this.proposal;this.proposal=null;
    return this.remember(text,await this.execute(canonicalTodo(proposal)));
   }
   return this.remember(text,{ok:true,kind:'proposal',proposalId:this.proposal.proposalId,message:`${intent.due_date||'마감일 없이'}, ‘${intent.title}’ 할 일을 등록할까요?`});
  }
  this.proposal=null;
  if(intent.type==='briefing')return this.remember(text,{...briefing,message:briefing.message.replace('이번 시험 브리핑에는 구글 캘린더와 메일은 포함되지 않습니다.','')+'\n캘린더: '+(context.calendar?.errors.length?'조회하지 못했어요.':context.calendar?.items.map(x=>{const e=JSON.parse(x.text);return calendarLine({...e,summary:x.title});}).join('\n')||'조회된 일정 없음'),sources:context.records?.items});
  if(!['chat','clarify'].includes(intent.type))return this.remember(text,{ok:false,message:'일정 수정과 삭제는 아직 연결되지 않았어요. 기존 사이트에서 처리해주세요.'});
  return this.remember(text,{ok:true,message:result.reply,sources:context.records?.items,coverage:context.records?.errors});
 }
}
module.exports={Assistant,canonicalTodo,todayKst,calendarLine};

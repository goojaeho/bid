// The model proposes an interpretation; only this controller can execute it.
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
 constructor({infer,execute}){this.infer=infer;this.execute=execute;this.history=[];this.proposal=null;}
 reset(){this.history=[];this.proposal=null;}
 remember(text,result){this.history.push({role:'user',content:text},{role:'assistant',content:result.message});this.history=this.history.slice(-12);return result;}
 async command(text){
  const clean=text.trim().replace(/[.!?]+$/,'');
  if(this.proposal&&/^(응|네|예|좋아|등록해줘|넣어줘|그렇게 해|그래)$/.test(clean)){
   const proposal=this.proposal;this.proposal=null;
   return this.remember(text,await this.execute(canonicalTodo(proposal)));
  }
  if(this.proposal&&/^(아니|아니요|취소|취소해|됐어|넣지 마)$/.test(clean)){
   this.proposal=null;return this.remember(text,{ok:true,message:'네, 등록하지 않을게요.'});
  }
  if(/^(브리핑(\s*해봐|\s*해줘)?|오늘 할 일 알려줘)$/.test(clean))return this.remember(text,await this.execute('브리핑해봐'));
  const briefing=await this.execute('브리핑해봐');
  const result=await this.infer({messages:[...this.history,{role:'user',content:text}],context:{today:todayKst(),timezone:'Asia/Seoul',briefing:briefing.ok?briefing.message:'기존 할 일 조회 실패. 기록을 추측하지 마세요.',pending_todo:this.proposal}});
  const intent=result.intent;
  if(!intent||typeof result.reply!=='string'||result.reply.length>4000)throw Error('대화 해석 결과를 확인하지 못했어요.');
  if(intent.type==='add_todo'){
   canonicalTodo(intent);
   this.proposal={title:intent.title,due_date:intent.due_date||null};
   // Explicitness is checked against this user turn, not trusted from model output.
   if(/(?:등록|추가|넣어)\s*해?\s*줘/.test(clean)&&!/(?:말고|하지\s*마|넣지|않|까요|방법)/.test(clean)){
    const proposal=this.proposal;this.proposal=null;
    return this.remember(text,await this.execute(canonicalTodo(proposal)));
   }
   return this.remember(text,{ok:true,kind:'proposal',message:`${intent.due_date||'마감일 없이'}, ‘${intent.title}’ 할 일을 등록할까요?`});
  }
  this.proposal=null;
  if(intent.type==='briefing')return this.remember(text,briefing);
  if(!['chat','clarify'].includes(intent.type))return this.remember(text,{ok:false,message:'일정 수정과 삭제는 아직 연결되지 않았어요. 기존 사이트에서 처리해주세요.'});
  return this.remember(text,{ok:true,message:result.reply});
 }
}
module.exports={Assistant,canonicalTodo,todayKst};

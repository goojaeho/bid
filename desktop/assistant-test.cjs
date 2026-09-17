const test=require('node:test'),assert=require('node:assert/strict');
const {Assistant,canonicalTodo}=require('./assistant.cjs');
test('proposal has no write until user agrees; approval consumed once',async()=>{
 const calls=[];const a=new Assistant({infer:async()=>({reply:'제안',intent:{type:'add_todo',title:'견적서 보내기'}}),execute:async text=>{calls.push(text);return {ok:true,message:'실제 결과'};}});
 assert.equal((await a.command('견적서 보내야 해')).kind,'proposal');
 assert.deepEqual(calls,['브리핑해봐']);await a.command('응');
 assert.equal(calls[1],'견적서 보내기 할 일 등록해줘');assert.equal(a.proposal,null);
});
test('explicit request executes, negative request never does',async()=>{
 for(const text of ['견적서 등록해줘','견적서 등록하지 마']){
  const calls=[];const a=new Assistant({infer:async()=>({reply:'등록했어요',intent:{type:'add_todo',title:'견적서',explicit:true}}),execute:async t=>{calls.push(t);return {ok:true,message:'서버 결과'};}});
  const result=await a.command(text);assert.equal(calls.length,text.includes('하지')?1:2);
  assert.notEqual(result.message,'등록했어요');
 }
});
test('date validation preserves website parser semantics',()=>{
 assert.equal(canonicalTodo({title:'견적서',due_date:'2026-09-18'},'2026-09-17'),'9월 18일 견적서 할 일 등록해줘');
 for(const due_date of ['2026-09-16','2026-02-30','2028-01-01'])assert.throws(()=>canonicalTodo({title:'견적서',due_date},'2026-09-17'));
 assert.throws(()=>canonicalTodo({title:'내일 견적서',due_date:'2026-09-18'},'2026-09-17'));
});
test('related question receives real context and bounded recent conversation',async()=>{
 const requests=[];const a=new Assistant({infer:async req=>{requests.push(req);return {reply:'일반적인 준비 제안이에요.',intent:{type:'chat'}};},execute:async()=>({ok:true,message:'실제 할 일 정보'})});
 await a.command('뭐 준비해?');await a.command('좀 더 자세히');
 assert.equal(requests[1].messages.length,3);assert.equal(requests[1].context.briefing,'실제 할 일 정보');
 a.reset();assert.equal(a.history.length,0);
});
test('invalid model output and failed writes cannot produce completion',async()=>{
 const a=new Assistant({infer:async()=>({reply:'완료',intent:{type:'add_todo',title:'견적서'}}),execute:async()=>({ok:false,message:'연결 실패'})});
 assert.equal((await a.command('견적서 등록해줘')).ok,false);
 a.infer=async()=>({reply:'완료',intent:{type:'delete'}});assert.equal((await a.command('그거 지워줘')).ok,false);
});

const test=require('node:test'),assert=require('node:assert/strict');
const {Records}=require('./records.cjs');const {Assistant}=require('./assistant.cjs');
test('record pagination searches only mail and meetings including old pages',async()=>{
 const calls=[];const r=new Records(async(op,p)=>{calls.push(p);return {ok:true,items:[{source:p.source,id:String(p.offset),title:p.offset?'옛 견적서':'새 항목',text:p.offset?'견적서 회의내용':'기타',url:'https://www.oneaigen.com/mail'}],next:p.offset?null:25};});
 const result=await r.search('견적서 찾아줘');assert.equal(calls.length,4);assert.deepEqual([...new Set(calls.map(x=>x.source))],['mail','meetings']);assert.equal(result.matches,2);assert.equal(result.total,4);
});
test('partial records error is visible, not represented as no records',async()=>{
 const r=new Records(async()=>({ok:false,message:'연결 실패'}));const found=await r.search('견적서');assert.equal(found.truncated,true);assert.equal(found.errors.length,2);
});
test('calendar proposal does not write until matching approval; stale button cannot approve new proposal',async()=>{
 const calls=[];const a=new Assistant({service:async(op,p)=>{calls.push(op);return op==='calendar.preview'?{ok:true,conflicts:[],token:'signed'}:{ok:true,message:'saved'};}});
 const first=await a.prepareEvent({title:'첫일정',date:'2026-09-18'});const next=await a.prepareEvent({title:'두번째',date:'2026-09-19'});
 assert.equal((await a.approve(first.proposalId)).ok,false);assert.equal(calls.includes('calendar.commit'),false);
 assert.equal((await a.approve(next.proposalId)).ok,true);assert.equal(calls.at(-1),'calendar.commit');assert.equal((await a.approve(next.proposalId)).ok,false);
});
test('calendar conflicts never create approvable proposal',async()=>{
 const a=new Assistant({service:async()=>({ok:true,conflicts:[{summary:'기존회의'}],token:'x'})});
 assert.equal((await a.prepareEvent({title:'새회의',date:'2026-09-18'})).ok,false);assert.equal(a.proposal,null);
});

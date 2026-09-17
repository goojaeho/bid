(function(root){
 function wakeCommand(text){const match=String(text).trim().match(/^자\s*비\s*스(?:야)?[,.!?\s]*(.*)$/);return match?match[1].trim().replace(/^프리핑(?=\s|해|$)/,'브리핑'):null;}
 function wav(parts,sampleRate){const length=parts.reduce((n,p)=>n+p.length,0);const source=new Float32Array(length);let offset=0;for(const p of parts){source.set(p,offset);offset+=p.length;}
  const count=Math.floor(length*16000/sampleRate),buffer=new ArrayBuffer(44+count*2),view=new DataView(buffer);
  const word=(at,s)=>{for(let i=0;i<s.length;i++)view.setUint8(at+i,s.charCodeAt(i));};word(0,'RIFF');view.setUint32(4,36+count*2,true);word(8,'WAVE');word(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);word(36,'data');view.setUint32(40,count*2,true);
  for(let i=0;i<count;i++){const sample=Math.max(-1,Math.min(1,source[Math.floor(i*sampleRate/16000)]||0));view.setInt16(44+i*2,sample<0?sample*32768:sample*32767,true);}return new Uint8Array(buffer);
 }
 const api={wakeCommand,wav};if(typeof module!=='undefined')module.exports=api;else root.VoiceUtils=api;
})(globalThis);

// Flap「ID AGENT CURVE」（只限 Agent ID 的曲线发射台）新盘监控。只读，不交易。
import fs from "fs";
const RPC="https://bsc-rpc.publicnode.com", READER="0x18aa6c93f1887a243fdc348c9a77f5136555174a";
const Q={'0x4902c5ebc598265ed2212b559b042de8a5eeec3f':'BNCB','0x02fca66c1d1afb4e2a7884261eb00f63598a7436':'NVDAB','0x205812cdbed920aff76c6580abd681a46d11efc7':'QQQB','0xbe9d156892e55e7154bcd3cb0fea677f9d3103e1':'SPCXB'};
import fs0 from "fs"; const SF=process.env.AIDBOT_DATA?(fs0.mkdirSync(process.env.AIDBOT_DATA+"/flap",{recursive:true}),process.env.AIDBOT_DATA+"/flap/state.json"):new URL("./state.json",import.meta.url).pathname;
const w=n=>BigInt(n).toString(16).padStart(64,'0'), wa=a=>a.toLowerCase().replace(/^0x/,'').padStart(64,'0');
const call=async(to,data)=>{const r=await fetch(RPC,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'eth_call',params:[{to,data},'latest']})});return (await r.json()).result;};
const dec=(hex,o)=>{const h=hex.replace(/^0x/,'');const len=Number(BigInt('0x'+h.slice(o*2,o*2+64)));return Buffer.from(h.slice(o*2+64,o*2+64+len*2),'hex').toString('utf8');};
const S=fs.existsSync(SF)?JSON.parse(fs.readFileSync(SF)):{total:null,names:{}};
const p0=await call(READER,'0x8f82e165'+w(0)+w(1)); const total=Number(BigInt('0x'+p0.slice(2+128,2+192)));
const out=[]; const start=S.total===null?Math.max(0,total-3):S.total;
for(let i=start;i<total;i++){try{
 const pg=await call(READER,'0x8f82e165'+w(i)+w(1)); const W=pg.slice(2).match(/.{64}/g); const off=Number(BigInt('0x'+W[0]))/32; const m='0x'+W[off+1].slice(24);
 const st=await call(READER,'0x51c023e2'+wa(m)); const SW=st.slice(2).match(/.{64}/g);
 const t0=Number(BigInt('0x'+SW[0]))/32, inner=t0+Number(BigInt('0x'+SW[t0]))/32;
 const name=dec(st,(inner+Number(BigInt('0x'+SW[inner]))/32)*32), sym=dec(st,(inner+Number(BigInt('0x'+SW[inner+1]))/32)*32);
 const buy=Number(BigInt('0x'+SW[inner+5]))/100, sell=Number(BigInt('0x'+SW[inner+6]))/100, maxW=Number(BigInt('0x'+SW[inner+11]))/100;
 const q='0x'+SW[t0+1].slice(24); const qn=/^0x0{40}$/.test(q)?'BNB':Q[q]||q.slice(0,10);
 const key=(sym||name).toLowerCase(); const dup=S.names[key]; S.names[key]=(dup||0)+1;
 const flags=[]; if(buy+sell>8)flags.push(`税偏高 ${buy}%/${sell}%`); if(dup)flags.push('跟风同名'); if(/<|script|onerror|http/i.test(name+sym))flags.push('名称含可疑代码，别在网页点');
 out.push({index:i,market:m,name:name.slice(0,40),symbol:sym.slice(0,20),quote:qn,buyTax:buy,sellTax:sell,maxWalletPct:maxW,flags,verdict:flags.length>=2?'跳过':flags.length?'有疑点':'首发、税合理',launchpadToken:/^(flap|flapx)$/i.test(sym.trim())});
}catch(e){out.push({index:i,error:e.message});}}
S.total=total; fs.writeFileSync(SF,JSON.stringify(S));
console.log(JSON.stringify({total,new:out}));

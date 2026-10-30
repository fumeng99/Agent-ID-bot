// 通用 PoW 免费铸造（AgentHash 式：challengeOf / status.minerTarget / verify / mine(uint256)）
// 用法: node powmint.mjs <contract> <accountDir> <address> [--dry] [--max N]
import { ethers } from "ethers"; import { execFileSync } from "child_process";
const [,,C,ACC,ADDR,...rest]=process.argv; const dry=rest.includes("--dry"); const max=Number(rest[rest.indexOf("--max")+1]||1)||1;
const ABI=["function challengeOf(address) view returns (bytes32)","function verify(address,uint256) view returns (bool ok,uint256 work,uint256 target)","function mine(uint256 nonce) payable returns (uint256)",
"function status(address miner) view returns (tuple(bool open,uint256 issued,uint256 drawn,uint256 maxSupply,uint256 blockNumber,uint256 timestamp,uint256 networkTarget,uint256 minerTarget,bytes32 minerChallenge,uint256 phaseBits,uint256 phase1Bits,uint256 bitsPerPhase,uint256 pending,bool drawReady,uint256 nextDrawBlock,tuple(uint8 phase,uint8 reason,bool free,uint256 priceUsd,uint256 priceWei,uint8 priceSource) quote,tuple(uint16 freePhase1,uint16 freeLater,uint16 paidTotal,uint8 freeLaterPhase) wallet,uint256 freeLeft,uint256 paidThisPhase,uint256 bnbUsd,uint8 priceSource,bool agentHolder,uint256 minedBy))"];
const p=new ethers.FallbackProvider(["https://bsc-dataseed.bnbchain.org","https://bsc-rpc.publicnode.com","https://bsc.blockrazor.xyz","https://bsc.drpc.org"].map((u,i)=>({provider:new ethers.JsonRpcProvider(u,56,{staticNetwork:true}),priority:i+1,weight:1,stallTimeout:2500})),56,{quorum:1}); const c=new ethers.Contract(C,ABI,p);
const W=process.env.AIDBOT_HOME||(process.env.HOME+"/.agent-id-bot"); const BAW=`${W}/baw/node_modules/.bin/baw`; const env={...process.env,HOME:`${W}/baw/accounts/${ACC}`};
const out=[];
for(let i=0;i<max;i++){
  const s=await c.status(ADDR);
  if(!s.open||!s.quote.free||s.freeLeft==0n||!s.agentHolder){out.push({stop:"not_free",open:s.open,free:s.quote.free,freeLeft:s.freeLeft+"",agent:s.agentHolder});break;}
  const ch=await c.challengeOf(ADDR); const tgt=s.minerTarget;
  const bits=(256-tgt.toString(2).length);
  const t0=Date.now(); const n=execFileSync("python3",[new URL("./miner.py", import.meta.url).pathname,ADDR,ch,tgt.toString(16),"900"]).toString().trim();
  if(n==="NOTFOUND"){out.push({stop:"mine_timeout",bits});break;}
  const v=await c.verify(ADDR,BigInt(n)); if(!v.ok){out.push({stop:"verify_failed",n});break;}
  const data=c.interface.encodeFunctionData("mine",[BigInt(n)]);
  const pv=JSON.parse(execFileSync(BAW,["contract-call","preview","--binanceChainId","56","--from",ADDR,"--to",C,"--value","0","--inputData",data,"--gasLimit","500000","--json"],{env}).toString());
  const sr=pv.data?.simulationResult, rk=pv.data?.risks;
  const clean=sr&&sr.simulationCode==="000000000"&&!sr.allowanceChanges?.length&&!sr.authorityChanges?.length&&!rk?.riskDetails?.length&&!rk?.riskBehaviors?.length&&(sr.balanceChanges||[]).every(b=>b.change>0);
  if(!clean){out.push({stop:"preview_not_clean",sim:sr?.simulationCode,err:sr?.simulationErrorDetail});break;}
  if(dry){out.push({dry:true,bits,secs:(Date.now()-t0)/1000,requestId:pv.data.requestId});break;}
  const ex=JSON.parse(execFileSync(BAW,["contract-call","execute","--requestId",pv.data.requestId,"--json"],{env}).toString());
  out.push({minted:true,bits,secs:(Date.now()-t0)/1000,tx:ex.data?.txHash});
  await new Promise(r=>setTimeout(r,8000));
}
console.log(JSON.stringify(out));

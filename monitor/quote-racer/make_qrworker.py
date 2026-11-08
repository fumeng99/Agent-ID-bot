#!/usr/bin/env python3
"""由 baw 的 dist/index.js 生成常驻 worker（qrworker.mjs）：去掉 CLI 入口 kr.parse()，改为 stdin 每行一个 JSON 请求。
baw 升级后 racer.py 会自动重新生成。入口变量名（kr/ce/I）随版本可能变，生成后自检失败就退回 CLI。"""
import os, sys
D = os.environ.get('BAW_DIST', '$AIDBOT_HOME/baw/node_modules/@binance/agentic-wallet/dist')
s = open(os.path.join(D, 'index.js')).read().rstrip()
if not s.endswith('kr.parse();') or 'var ce=class' not in s or 'async function I()' not in s: sys.exit('baw 版本结构变了，不能生成 worker')
W = "\nimport {createInterface as __ci} from 'node:readline';\n// quote-racer 常驻 worker：一个账号一个进程（HOME 决定会话），省去每次 CLI 冷启动与 TLS 握手；preview 干净才 execute（dry 不 execute）\nconst __out=o=>process.stdout.write(JSON.stringify(o)+'\\n');\ntry{await I()}catch(e){__out({ready:false,err:String(e&&e.message||e)});process.exit(1)}\nconst __api=new ce();\n__out({ready:true});\n__ci({input:process.stdin}).on('line',async ln=>{let j;try{j=JSON.parse(ln)}catch{return}\n const t0=Date.now();\n try{const p=await __api.contractCallPreview(j.body);const d=(p&&p.data)||p;const sr=d.simulationResult||{};\n  const rd=(d.risks||{}).riskDetails;const ac=sr.authorityChanges;\n  const clean=!String(sr.simulationCode??'0').replace(/0/g,'').trim()&&!(rd&&(Array.isArray(rd)?rd.length:Object.keys(rd).length))&&!(ac&&(Array.isArray(ac)?ac.length:Object.keys(ac).length));\n  const t1=Date.now();\n  if(!clean||j.dry){__out({id:j.id,stage:'preview',clean,ms:t1-t0,detail:clean?undefined:JSON.stringify(sr).slice(0,200)});return}\n  const e=await __api.contractCallExecute({requestId:d.requestId});const ed=(e&&e.data)||e;\n  __out({id:j.id,stage:'execute',txHash:ed&&ed.txHash,ms_preview:t1-t0,ms:Date.now()-t0})\n }catch(err){__out({id:j.id,stage:'error',ms:Date.now()-t0,err:String(err&&err.message||err).slice(0,200)})}});\n"
open(os.path.join(D, 'qrworker.mjs'), 'w').write(s[:-len('kr.parse();')] + W)
print('ok')

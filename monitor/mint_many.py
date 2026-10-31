# usage: mint_many.py <contract> <calldata> [acct labels...]  — preview→(clean)→execute per account
import sys,json,subprocess,os,time
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from confirm import confirm_tx, bal_of
C=sys.argv[1].lower(); D=sys.argv[2]
accts=json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
B='$AIDBOT_HOME/baw/node_modules/.bin/baw'
out=[]
only=sys.argv[3:]
for a in accts:
    if only and a['label'] not in only: continue
    env=dict(os.environ,HOME=a['home'],npm_config_cache='/tmp/npmcache')
    def run(args):
        r=subprocess.run([B]+args+['--json'],env=env,capture_output=True,text=True,timeout=120)
        try: return json.loads(r.stdout)
        except Exception: return {'success':False,'raw':(r.stdout+r.stderr)[:300]}
    p=run(['contract-call','preview','--binanceChainId','56','--from',a['bsc'],'--to',C,'--value','0','--inputData',D])
    if not p.get('success'): out.append((a['label'],'preview_fail',str(p)[:200])); continue
    d=p['data']; sr=d['simulationResult']
    bc=sr.get('balanceChanges',[])
    ok=(sr.get('simulationCode')=='000000000' and not sr.get('allowanceChanges') and not sr.get('authorityChanges')
        and not d['risks'].get('riskDetails') and not d['risks'].get('riskBehaviors') and not any(d['risks'].get('addresses',{}).values() if d['risks'].get('addresses') else [])
        and all(x.get('change',0)>0 for x in bc) and bc)
    if not ok: out.append((a['label'],'unclean',json.dumps(sr)[:300])); continue
    b0=bal_of(C,a['bsc'])
    e=run(['contract-call','execute','--requestId',d['requestId']])
    tx=(e.get('data') or {}).get('txHash') if isinstance(e,dict) else None
    if not tx: out.append((a['label'],'execute_fail',json.dumps(e)[:300])); continue
    c=confirm_tx(tx,C,a['bsc'],b0)
    out.append((a['label'],c['state'],tx,c.get('detail','')))
    time.sleep(2)
for o in out: print(o)

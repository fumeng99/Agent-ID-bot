# 付费/持仓项目风险速查：开发者权限 + 貔貅（模拟把币转进池子）
# 用法: python3 riskcheck.py <代币合约> [持有人地址]
import sys,json,urllib.request,os
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__))); import codeutil as CU
RPCS=['https://bsc-rpc.publicnode.com','https://bsc-dataseed.bnbchain.org','https://bsc.drpc.org','https://1rpc.io/bnb']
FACT='0xca143ce32fe78f1f7019d7d551a6402fc5350c73';WBNB='0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c';USDT='0x55d398326f99059ff775485246999027b3197955'
def rpc(m,p):
    e=None
    for u in RPCS:
        try:
            j=json.load(urllib.request.urlopen(urllib.request.Request(u,json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),{'content-type':'application/json','User-Agent':'Mozilla/5.0'}),timeout=10))
            if 'result' in j: return j['result']
            e=j.get('error'); 
            if m=='eth_call': raise RuntimeError(str(e))
        except RuntimeError: raise
        except Exception as x: e=x
    raise RuntimeError(str(e))
w=lambda a:a.lower().replace('0x','').rjust(64,'0')
t=sys.argv[1].lower();holder=sys.argv[2].lower() if len(sys.argv)>2 and not sys.argv[2].startswith('--') else None
code=rpc('eth_getCode',[t,'latest']).lower();out={'token':t,'flags':[],'honeypot':None}
has=lambda ss:any(('63'+x) in code for x in ss)
if has(['40c10f19']): out['flags'].append('可增发')
if has(['8456cb59','02329a29']): out['flags'].append('可暂停交易')
if has(['f9f92be4','44337ea1','0ecb93c0','1a895266']): out['flags'].append('有黑名单')
if has(['8c0b5e22','c0246668','5342acb4','aa4b6fca','061c82d0']): out['flags'].append('可改税/限额')
try:
    ow=rpc('eth_call',[{'to':t,'data':'0x8da5cb5b'},'latest']); out['owner']='0x'+ow[-40:]
    if int(ow,16)==0: out['flags']=[f+'（已放弃权限）' for f in out['flags']]
except Exception: out['owner']=None
pair=None
for q in (WBNB,USDT,'0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d'):
    try:
        r=rpc('eth_call',[{'to':FACT,'data':'0xe6a43905'+w(t)+w(q)},'latest'])
        if int(r,16): pair='0x'+r[-40:];break
    except Exception: pass
out['pair']=pair
if pair and holder:
    try:
        b=int(rpc('eth_call',[{'to':t,'data':'0x70a08231'+w(holder)},'latest']),16)
        if b>0:
            try: rpc('eth_call',[{'from':holder,'to':t,'data':'0xa9059cbb'+w(pair)+hex(max(b//100,1))[2:].rjust(64,'0')},'latest']); out['honeypot']=False
            except Exception as e: out['honeypot']=True; out['hp_err']=str(e)[:120]
    except Exception: pass
# 2026-11-01 新增（参考字段，不改变买入条件）：
#  upgradeable_proxy：可升级代理（EIP-1967 实现槽/Beacon 槽有值，或 EIP-1167），项目方可换掉合约逻辑
#  has_refund：合约（代理追到底）里有无退款函数 refund()/claimRefund()/refundOpen() 等（保本退款，仅供参考）
#  用法补充：python3 riskcheck.py <代币> [持有人] [--mint <fair mint 合约>]  → 同时给出 mint 合约的 has_refund/upgradeable_proxy
def _call(to,d):
    try: return rpc('eth_call',[{'to':to,'data':d},'latest'])
    except Exception: return None
def proxy_refund(a):
    c=(rpc('eth_getCode',[a,'latest']) or '0x').lower(); up=False
    for slot in (CU.IMPL_SLOT,CU.BEACON_SLOT):
        try:
            v=rpc('eth_getStorageAt',[a,slot,'latest'])
            if v and int(v,16): up=True
        except Exception: pass
    if c[2:].startswith('363d3d373d3d3d363d73'): up=True
    fc=CU.full_code(rpc,_call,a,c); r=CU.refund_info(fc)
    return {'upgradeable_proxy':up,**r}
try: out.update(proxy_refund(t))
except Exception as e: out['proxy_refund_err']=str(e)[:80]
if '--mint' in sys.argv:
    m=sys.argv[sys.argv.index('--mint')+1].lower()
    try: out['mint_contract']={'address':m,**proxy_refund(m)}
    except Exception as e: out['mint_contract']={'address':m,'err':str(e)[:80]}
print(json.dumps(out,ensure_ascii=False))

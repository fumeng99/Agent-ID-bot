# 交易确认：等 receipt，核对 status 和到账。state: 已确认到账 / 已上链-余额未变 / 失败(revert) / 未确认(超时，不要重发)
import time,os,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
TRANSFER='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
T1155S='0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62'
T1155B='0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb'
def bal_of(c,who):
    try:
        r=batch([('eth_call',[{'to':c,'data':'0x70a08231'+who[2:].lower().rjust(64,'0')},'latest'])],tolerant=True)[0]
        return int(r,16) if r and r!='0x' else None
    except Exception: return None
def confirm_tx(tx,c,who,b0=None,timeout=60):
    t=time.time(); rc=None
    while time.time()-t<timeout:
        try: rc=batch([('eth_getTransactionReceipt',[tx])],tolerant=True)[0]
        except Exception: rc=None
        if rc: break
        time.sleep(3)
    if not rc: return {'state':'未确认(超时，不要重发)'}
    gas=int(rc['gasUsed'],16)*int(rc.get('effectiveGasPrice','0x0'),16)/1e18
    if rc.get('status')!='0x1': return {'state':'失败(revert)','detail':f'gas {gas:.6f} BNB'}
    w='0x'+who[2:].lower().rjust(64,'0')
    got=[l for l in rc.get('logs',[]) if l['topics'] and l['topics'][0] in (TRANSFER,T1155S,T1155B) and
         ((l['topics'][0]==TRANSFER and len(l['topics'])>2 and l['topics'][2].lower()==w) or
          (l['topics'][0]!=TRANSFER and len(l['topics'])>3 and l['topics'][3].lower()==w))]
    out=[l for l in rc.get('logs',[]) if l['topics'] and l['topics'][0]==TRANSFER and len(l['topics'])>1 and l['topics'][1].lower()==w]
    b1=bal_of(c,who)
    det=f'gas {gas:.6f} BNB; 转入事件 {len(got)}; 转出事件 {len(out)}; balanceOf {b0}->{b1}'
    if got or (b0 is not None and b1 is not None and b1>b0): return {'state':'已确认到账','detail':det,'gas':gas}
    return {'state':'已上链-余额未变','detail':det,'gas':gas}
if __name__=='__main__':
    # usage: confirm.py <txhash> <contract> <wallet>
    print(confirm_tx(sys.argv[1],sys.argv[2],sys.argv[3]))

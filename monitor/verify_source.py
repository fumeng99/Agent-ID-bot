# 源码验证检查（规则 B）。usage: verify_source.py <addr> [addr...]   输出每个地址一行 JSON
# 顺序：Sourcify v2 → OKLink（镜像 BscScan 已验证源码，免 key，429 自动退避）。代理合约(EIP-1967)同时查实现合约。
# 结果缓存在 verify_cache.json（已验证的永久缓存；未验证的 30 分钟后重查，因为项目方常在上线后才验证）。
import sys,os,json,time,urllib.request
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
D=os.path.dirname(os.path.abspath(__file__)); CF=os.path.join(D,'verify_cache.json'); FP=os.path.join(D,'verified_fp.json')
UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
IMPL='0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
def get(u,t=15):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=t).read())
def sourcify(a):
    try: return get(f'https://sourcify.dev/server/v2/contract/56/{a}').get('match') or None
    except urllib.error.HTTPError as e: return None if e.code==404 else 'ERR'
    except Exception: return 'ERR'
def oklink(a):
    for i in range(4):
        try:
            r=get(f'https://www.oklink.com/api/v5/explorer/contract/verify-contract-info?chainShortName=BSC&contractAddress={a}')
            if r.get('code')=='50011': time.sleep(2+2*i); continue
            d=r.get('data') or []
            return bool(d and (d[0].get('sourceCode') or '').strip())
        except Exception: time.sleep(2)
    return 'ERR'
def one(a,cache):
    a=a.lower(); c=cache.get(a)
    if c and (c['verified'] or time.time()-c['t']<1800): return c
    s=sourcify(a); src=None
    if s and s!='ERR': src='sourcify:'+s
    else:
        o=oklink(a)
        if o is True: src='bscscan/oklink'
        elif o=='ERR' and s=='ERR': return {'addr':a,'verified':None,'source':'查询失败','t':time.time()}
    if not src:
        # 2026-11-01 20:36 用户同意：未验证但字节码指纹（去元数据、PUSH20/32 立即数清零）与某个已验证合约完全一致 → 视为同一份已验证源码
        try:
            from codefp import fps
            fp=fps([a]).get(a); ref=json.load(open(FP)) if os.path.exists(FP) else {}
            hit=[x for x,f in ref.items() if fp and f==fp and x!=a]
            if hit: src='bytecode-match:'+hit[0]
        except Exception: pass
    elif not src.startswith('bytecode-match'):
        try:
            from codefp import fps
            fp=fps([a]).get(a)
            if fp:
                ref=json.load(open(FP)) if os.path.exists(FP) else {}; ref[a]=fp
                t=FP+'.%d.tmp'%os.getpid(); json.dump(ref,open(t,'w')); os.replace(t,FP)
        except Exception: pass
    r={'addr':a,'verified':bool(src),'source':src or '未验证','t':time.time(),
       'sourcify_url':f'https://repo.sourcify.dev/56/{a}','bscscan_url':f'https://bscscan.com/address/{a}#code'}
    cache[a]=r; return r
if __name__=='__main__':
    try: cache=json.load(open(CF)) if os.path.exists(CF) else {}
    except Exception:
        # 并发写坏时取第一个完整 JSON 对象，读不了就当空缓存（2026-11-01 多进程共用 .tmp 导致缓存损坏）
        try: cache=json.JSONDecoder().raw_decode(open(CF).read())[0]
        except Exception: cache={}
    addrs=[x.lower() for x in sys.argv[1:]]
    try: slots=batch([('eth_getStorageAt',[a,IMPL,'latest']) for a in addrs],tolerant=True)
    except Exception: slots=[None]*len(addrs)
    for a,sl in zip(addrs,slots):
        r=dict(one(a,cache))
        impl='0x'+sl[-40:] if sl and int(sl,16) else None
        if impl:
            ri=one(impl,cache); r['proxy_impl']=impl; r['impl_verified']=ri['verified']
            r['verified']=bool(r['verified'] and ri['verified'])
        r.pop('t',None); print(json.dumps(r,ensure_ascii=False))
    tmp=CF+'.%d.tmp'%os.getpid(); json.dump(cache,open(tmp,'w')); os.replace(tmp,CF)

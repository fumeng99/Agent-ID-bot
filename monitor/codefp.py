# 字节码指纹：去掉末尾编译元数据(CBOR)，把 PUSH20/PUSH32 的立即数清零（immutable/地址/部署参数），再 sha256。
# 用途：未验证合约若与某个「已验证」合约指纹完全一致 → 视为同一份源码（2026-11-01 20:36 用户同意）。
import sys,os,json,hashlib
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
def norm(h):
    b=bytes.fromhex(h[2:] if h.startswith('0x') else h)
    if len(b)>2:
        L=int.from_bytes(b[-2:],'big')
        if 0<L<len(b) and b[-L-2] in (0xa1,0xa2,0xa3): b=b[:-L-2]
    out=bytearray(); i=0
    while i<len(b):
        op=b[i]; out.append(op); i+=1
        if 0x60<=op<=0x7f:
            n=op-0x5f; arg=b[i:i+n]
            out+= (b'\0'*len(arg)) if n in (20,32) else arg; i+=n
    return hashlib.sha256(bytes(out)).hexdigest()
def codes(addrs):
    r=batch([('eth_getCode',[a,'latest']) for a in addrs],tolerant=True)
    return dict(zip(addrs,r))
def fps(addrs):
    return {a:(norm(c) if c and len(c)>4 else None) for a,c in codes(addrs).items()}
if __name__=='__main__':
    print(json.dumps(fps([a.lower() for a in sys.argv[1:]]),indent=1))

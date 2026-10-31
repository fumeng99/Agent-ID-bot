"""合约码工具（2026-11-01 学自  v2.4.0 fullCode / v2.5.1 findCapSel / 保本退款检测）。只读。
- full_code(rpc, call, a, code=None)：代理合约一律追到底——EIP-1967 实现槽、Beacon 槽、EIP-1167 最小代理、
  合约码开头写死的实现/Beacon 地址；返回拼接后的小写字节码（用来判断是否含 Agent ID 登记合约、找函数选择器）。
- create_addr(deployer, nonce)：普通 CREATE 部署出来的合约地址（rlp(deployer, nonce) 的 keccak 后 20 字节）。
- push4_selectors(code)：合约码里所有 PUSH4 常量（候选函数选择器）。
- REFUND_SELS：退款类函数选择器（refund()/claimRefund()/refundOpen() 等，照  v2.5.1 清单）。
"""
import sys as _s; _s.path.insert(0, '$AIDBOT_DATA/vendor'); _s.path.append('$AIDBOT_HOME/pylib')
from Crypto.Hash import keccak

REG = '60b9262456de011f7d15a54cdd8c48baaa8b022f'
IMPL_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
BEACON_SLOT = '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50'
#  v2.5.1 的退款选择器；名字是我们用 keccak 反查到的（反查不到的保留原值，含义以合约源码为准）
REFUND_SELS = ['590e1ae3', 'b5545a3c', '278ecde1', '5b7baf64', '110f8874', '16bfe25c', '38e771ab', 'b2d5ae44',
               'fa89401a', 'bffa55d5', '68ec0d99', '74c1b1c9', 'b30196e5', 'c033a490', '4c4a386f', 'd233e4eb']


def kec(b: bytes) -> bytes:
    h = keccak.new(digest_bits=256); h.update(b); return h.digest()


def sel(sig: str) -> str:
    return kec(sig.encode()).hex()[:8]


def _rlp_bytes(b: bytes) -> bytes:
    if len(b) == 1 and b[0] < 0x80: return b
    if len(b) < 56: return bytes([0x80 + len(b)]) + b
    l = len(b).to_bytes((len(b).bit_length() + 7) // 8, 'big'); return bytes([0xb7 + len(l)]) + l + b


def create_addr(deployer: str, nonce: int) -> str:
    nb = b'' if nonce == 0 else nonce.to_bytes((nonce.bit_length() + 7) // 8, 'big')
    payload = _rlp_bytes(bytes.fromhex(deployer[2:].lower())) + _rlp_bytes(nb)
    pre = bytes([0xc0 + len(payload)]) if len(payload) < 56 else None
    return '0x' + kec(pre + payload).hex()[-40:]


def push4_selectors(code: str):
    h = (code or '').lower().replace('0x', ''); out = []
    i = 0
    while i < len(h) - 1:
        op = int(h[i:i + 2], 16)
        if op == 0x63 and i + 10 <= len(h): out.append(h[i + 2:i + 10])
        if 0x60 <= op <= 0x7f: i += 2 + 2 * (op - 0x5f)
        else: i += 2
    return list(dict.fromkeys(out))


def full_code(rpc, call, a, code=None, depth=0):
    """rpc(method, params) / call(to, data)->hex|None 由调用方传入（各脚本节点池不同）。"""
    if code is None:
        try: code = rpc('eth_getCode', [a, 'latest']) or '0x'
        except Exception: code = '0x'
    code = code.lower(); out = code
    if len(code) < 4 or REG in code or depth > 1: return out
    targets = []
    for slot in (IMPL_SLOT,):
        try:
            v = rpc('eth_getStorageAt', [a, slot, 'latest'])
            if v and int(v, 16): targets.append('0x' + v[-40:])
        except Exception: pass
    try:
        v = rpc('eth_getStorageAt', [a, BEACON_SLOT, 'latest'])
        if v and int(v, 16):
            r = call('0x' + v[-40:], '0x5c60da1b')
            if r and len(r) >= 66 and int(r[:66], 16): targets.append('0x' + r[26:66])
    except Exception: pass
    h = code[2:]
    if h.startswith('363d3d373d3d3d363d73') and len(h) >= 60: targets.append('0x' + h[20:60])  # EIP-1167 最小代理
    if len(code) < 4000:  # 小合约：开头写死的 Beacon/实现地址（PUSH20 / PUSH32 填充）
        import re
        emb = re.findall(r'7f000000000000000000000000([0-9a-f]{40})', h[:1200]) + re.findall(r'73([0-9a-f]{40})', h[:1200])
        for x in list(dict.fromkeys(emb))[:3]:
            if int(x, 16) == 0: continue
            r = call('0x' + x, '0x5c60da1b')  # implementation()：是 Beacon 就取它的实现
            targets.append('0x' + r[26:66] if r and len(r) >= 66 and int(r[:66], 16) else '0x' + x)
    for t in list(dict.fromkeys(targets))[:4]:
        if REG in out: break
        try: out += full_code(rpc, call, t, None, depth + 1)
        except Exception: pass
    return out


def refund_info(code: str):
    """保本退款机制：合约码里是否有退款类函数。"""
    c = (code or '').lower(); hit = [s for s in REFUND_SELS if ('63' + s) in c]
    return {'has_refund': bool(hit), 'refund_selectors': ['0x' + s for s in hit]}


def find_cap(call, a, code, sold):
    """v2.5.1 自动找总量函数：合约码里的无参函数，返回值 > 已售、整百、≤1 千万，优先 10000。返回 (selector, value) 或 (None, None)。"""
    cand = []
    for s in push4_selectors(code)[:160]:
        r = call(a, '0x' + s)
        if not r or len(r) != 66: continue
        v = int(r, 16)
        if sold < v <= 10 ** 7 and v % 100 == 0: cand.append((s, v))
    if not cand: return None, None
    cand.sort(key=lambda x: (x[1] != 10000, x[1]))
    return '0x' + cand[0][0], cand[0][1]

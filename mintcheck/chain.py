"""BSC 只读链上工具：RPC 节点池（并发 batch、限流冷却）、keccak、ABI 编解码、代理追踪、字节码指纹、选择器反查。
本模块不签名、不发交易，只用 eth_call / eth_getCode / eth_getStorageAt / eth_estimateGas。"""
import json, os, sys, time, hashlib, re, urllib.request, threading
sys.path.append('$AIDBOT_HOME/pylib')
from Crypto.Hash import keccak as _k

D = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(D, 'library'); os.makedirs(LIB, exist_ok=True)
RPCS = ['https://bsc-dataseed.bnbchain.org', 'https://bsc-dataseed1.bnbchain.org', 'https://bsc-dataseed2.bnbchain.org',
        'https://bsc-dataseed1.defibit.io', 'https://bsc-dataseed1.ninicoin.io', 'https://bsc-rpc.publicnode.com',
        'https://bsc.blockrazor.xyz', 'https://bsc.drpc.org', 'https://bsc-mainnet.public.blastapi.io', 'https://bsc.meowrpc.com']
UA = {'content-type': 'application/json', 'user-agent': 'Mozilla/5.0'}
REGISTRY = '0x60b9262456de011f7d15a54cdd8c48baaa8b022f'   # SPACE ID .ai Agent ID 登记合约
USDT = '0x55d398326f99059ff775485246999027b3197955'; WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
FACTORY = '0xca143ce32fe78f1f7019d7d551a6402fc5350c73'
IMPL_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
BEACON_SLOT = '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50'
ADMIN_SLOT = '0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103'
_bad = {}; _lock = threading.Lock(); STATS = {'rpc_calls': 0, 'rpc_errors': 0}


def kec(b):
    h = _k.new(digest_bits=256); h.update(b if isinstance(b, bytes) else b.encode()); return h.hexdigest()


def sel(sig): return '0x' + kec(sig)[:8]


_rr = [0]
def _order():
    now = time.time(); good = [u for u in RPCS if _bad.get(u, 0) < now] or RPCS
    _rr[0] += 1; k = _rr[0] % len(good)
    return good[k:] + good[:k]


def _post(u, body, t=6):  # 2026-11-10：10→6 秒；代理隧道慢时整体卡死
    r = urllib.request.Request(u, json.dumps(body).encode(), UA)
    return json.load(urllib.request.urlopen(r, timeout=t))


def batch(calls, chunk=15, _retry=True):
    """calls=[(method, params)] -> 结果列表。"""
    # 2026-11-10：≥20 条纯 eth_call（不带 from/value）→ 走 launch-monitor/rpcpool 的 Multicall3（约 8 倍快，结果一致）；失败项与原来一样返回 {'error': ...}
    if _retry and len(calls) >= 20 and all(m == 'eth_call' and len(p) == 2 and isinstance(p[0], dict) and set(p[0]) <= {'to', 'data'} and p[1] == 'latest' for m, p in calls):
        try:
            import sys as _s
            if '$AIDBOT_DATA' not in _s.path: _s.path.append('$AIDBOT_DATA')
            import rpcpool as _rp
            r = _rp.mcall([(p[0]['to'], p[0]['data']) for _, p in calls], deadline=time.time() + 25)
            return [x if x is not None else {'error': 'revert_or_unavailable'} for x in r]
        except Exception: pass
    """calls=[(method, params)] -> 结果列表（单项报错/revert 返回 {'error': ...}）。换节点重试。"""
    parts = [calls[i:i + chunk] for i in range(0, len(calls), chunk)]
    if len(parts) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(min(6, len(parts))) as ex:
            return [x for r in ex.map(lambda p: batch(p, chunk, _retry), parts) for x in r]
    out = []
    for part in parts:
        body = [{'jsonrpc': '2.0', 'id': j, 'method': m, 'params': p} for j, (m, p) in enumerate(part)]
        res = None
        for u in (_order() * 2)[:6]:  # 2026-11-10：最多换 6 个节点（原来 2×全部节点 × 10 秒 = 一次调用可卡数分钟）
            try:
                STATS['rpc_calls'] += 1
                r = _post(u, body)
                if isinstance(r, dict): raise RuntimeError(str(r)[:200])
                m = {x['id']: x for x in r}
                if any('error' in x and re.search(r'(limit|too many|rate|-32005|timeout|busy|capacity)', str(x['error']).lower()) for x in r): raise RuntimeError('rate limit')
                res = [m.get(j, {}).get('result') if 'result' in m.get(j, {}) else {'error': m.get(j, {}).get('error')} for j in range(len(part))]
                break
            except Exception:
                STATS['rpc_errors'] += 1
                with _lock: _bad[u] = time.time() + 60
        if res is None: res = [{'error': 'all rpc failed'}] * len(part)
        out += res
    if _retry:
        bad = [i for i, x in enumerate(out) if isinstance(x, dict) and re.search(r'(all rpc failed|limit|too many|timeout|-32005)', str(x.get('error')).lower())]
        if bad:
            time.sleep(0.8)
            again = batch([calls[i] for i in bad], chunk, _retry=False)
            for i, x in zip(bad, again): out[i] = x
    return out


def rpc(m, p):
    r = batch([(m, p)])[0]
    if isinstance(r, dict) and 'error' in r: raise RuntimeError(str(r['error'])[:300])
    return r


def call(to, data, frm=None, value=0, block='latest'):
    """eth_call -> (ok, 返回数据或 revert 数据/原因)。"""
    tx = {'to': to, 'data': data}
    if frm: tx['from'] = frm
    if value: tx['value'] = hex(value)
    r = batch([('eth_call', [tx, block])])[0]
    if isinstance(r, dict) and 'error' in r:
        e = r['error'] or {}
        return False, (e.get('data') if isinstance(e, dict) else None) or (e.get('message') if isinstance(e, dict) else str(e))
    return True, r


# ---------- ABI 编解码（只覆盖预检需要的类型） ----------
def enc_addr(a): return a.lower().replace('0x', '').rjust(64, '0')
def enc_uint(n): return hex(int(n))[2:].rjust(64, '0')


def dec_uint(h):
    try: return int(h[2:66], 16) if h and len(h) >= 66 else None
    except Exception: return None


def dec_addr(h): return ('0x' + h[26:66]) if h and len(h) >= 66 else None


def dec_str(h):
    try:
        b = h[2:]
        if len(b) >= 192:
            n = int(b[64:128], 16); return bytes.fromhex(b[128:128 + 2 * n]).decode('utf8', 'ignore')
        return bytes.fromhex(b[:64]).rstrip(b'\0').decode('utf8', 'ignore')
    except Exception: return ''


ERR_SIGS = {'0x08c379a0': 'Error(string)', '0x4e487b71': 'Panic(uint256)'}


def decode_revert(data, sigdb=None):
    """revert 数据 -> 人能读的原因。"""
    if not data or not isinstance(data, str) or data == '0x': return 'revert(无数据)'
    if not data.startswith('0x'): return data[:200]
    s = data[:10]
    if s == '0x08c379a0': return 'Error: ' + dec_str('0x' + data[10:])
    if s == '0x4e487b71': return f'Panic({dec_uint("0x" + data[10:])})'
    nm = (sigdb or {}).get(s) or lookup_sigs([s], kind='event_or_error').get(s)
    return f'{nm or s}' + (f' args={data[10:74]}' if len(data) > 10 else '')


# ---------- 选择器反查（openchain，带本地缓存） ----------
SIGCACHE = os.path.join(LIB, 'sigcache.json')


def _load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def _save(p, o):
    with _lock:
        t = f'{p}.{os.getpid()}.{threading.get_ident()}.tmp'; json.dump(o, open(t, 'w'), ensure_ascii=False, indent=1); os.replace(t, p)


def lookup_sigs(sels, kind='function'):
    cache = _load(SIGCACHE, {}); need = [s for s in sels if s not in cache]
    for i in range(0, len(need), 60):
        part = need[i:i + 60]
        try:
            u = 'https://api.openchain.xyz/signature-database/v1/lookup?function=' + ','.join(part) + '&filter=true'
            r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'user-agent': 'Mozilla/5.0'}), timeout=12))
            fs = r.get('result', {}).get('function', {})
            for s in part:
                c = fs.get(s) or []
                ver = [x['name'] for x in c if x.get('hasVerifiedContract')] or [x['name'] for x in c]
                cache[s] = ver[0] if ver else ''
        except Exception: pass
    _save(SIGCACHE, cache)
    return {s: cache.get(s, '') for s in sels}


# ---------- 合约码 ----------
def push4(code):
    """合约码里的 PUSH4 常量（候选函数选择器），按出现顺序去重。"""
    b = bytes.fromhex(code[2:]) if code and code.startswith('0x') else b''
    i = 0; out = []
    while i < len(b):
        op = b[i]; i += 1
        if op == 0x63 and i + 4 <= len(b):
            s = '0x' + b[i:i + 4].hex()
            if s not in out: out.append(s)
        if 0x60 <= op <= 0x7f: i += op - 0x5f
    return out


def fingerprint(code):
    """去 CBOR 元数据、PUSH20/32 立即数清零后 sha256（同一份源码不同部署参数 => 同指纹）。"""
    if not code or len(code) < 6: return None
    b = bytes.fromhex(code[2:])
    if len(b) > 2:
        L = int.from_bytes(b[-2:], 'big')
        if 0 < L < len(b) and b[-L - 2] in (0xa1, 0xa2, 0xa3): b = b[:-L - 2]
    out = bytearray(); i = 0
    while i < len(b):
        op = b[i]; out.append(op); i += 1
        if 0x60 <= op <= 0x7f:
            n = op - 0x5f; arg = b[i:i + n]; out += (b'\0' * len(arg)) if n in (20, 32) else arg; i += n
    return hashlib.sha256(bytes(out)).hexdigest()


def resolve(addr):
    """读代码并追代理：EIP-1967 实现槽、Beacon、EIP-1167 最小代理。返回 dict。"""
    a = addr.lower()
    r = batch([('eth_getCode', [a, 'latest']), ('eth_getStorageAt', [a, IMPL_SLOT, 'latest']),
               ('eth_getStorageAt', [a, BEACON_SLOT, 'latest']), ('eth_getStorageAt', [a, ADMIN_SLOT, 'latest'])])
    code = r[0] if isinstance(r[0], str) else '0x'
    info = {'address': a, 'code_len': max(0, (len(code) - 2) // 2), 'is_contract': len(code) > 2, 'proxy': None, 'impl': None, 'admin': None}
    def slot(v): return ('0x' + v[-40:]) if isinstance(v, str) and len(v) >= 42 and int(v, 16) else None
    impl = slot(r[1]); beacon = slot(r[2]); info['admin'] = slot(r[3])
    if impl: info.update(proxy='eip1967', impl=impl)
    elif beacon:
        ok, d = call(beacon, '0x5c60da1b'); info.update(proxy='beacon', impl=dec_addr(d) if ok else None, beacon=beacon)
    else:
        m = re.match(r'^0x363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3', code)
        if m: info.update(proxy='eip1167', impl='0x' + m.group(1))
    info['code'] = code
    if info['impl']:
        ic = rpc('eth_getCode', [info['impl'], 'latest']) or '0x'; info['impl_code'] = ic
    eff = info.get('impl_code') or code
    info['effective_code'] = eff
    info['fingerprint'] = fingerprint(eff)
    info['selectors'] = push4(eff)
    info['has_registry'] = REGISTRY[2:] in eff.lower() or REGISTRY[2:] in code.lower()
    return info


# ---------- 源码验证 / ABI（Sourcify v2，带缓存） ----------
VCACHE = os.path.join(LIB, 'verify_cache.json')


def sourcify(addr):
    a = addr.lower(); c = _load(VCACHE, {})
    if a in c and (c[a].get('verified') or time.time() - c[a].get('t', 0) < 1800): return c[a]
    res = {'verified': False, 't': time.time()}
    try:
        u = f'https://sourcify.dev/server/v2/contract/56/{a}?fields=abi,compilation'
        j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'user-agent': 'Mozilla/5.0'}), timeout=15))
        if j.get('match'):
            res = {'verified': True, 'match': j.get('match'), 'abi': j.get('abi') or [], 't': time.time(),
                   'name': (j.get('compilation') or {}).get('fullyQualifiedName', '').split(':')[-1]}
    except urllib.error.HTTPError as e:
        res['err'] = None if e.code == 404 else f'http {e.code}'
    except Exception as e: res['err'] = str(e)[:80]
    if not res.get('err'): c[a] = res; _save(VCACHE, c)
    return res


def abi_sig(f):
    def t(x):
        if x['type'].startswith('tuple'): return '(' + ','.join(t(c) for c in x.get('components', [])) + ')' + x['type'][5:]
        return x['type']
    return f"{f['name']}({','.join(t(i) for i in f.get('inputs', []))})"

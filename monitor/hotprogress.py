#!/usr/bin/env python3
"""免费盘铸造进度（只读 eth_call / getLogs）。2026-11-02 14:33 用户要求：「未验证热门免费盘（破例铸提示）」每行显示 已铸/总量、百分比、近 30 分钟增速。
读法（依次）：
  1. 常见无参函数：已铸 totalMinted/totalSupply/minted/totalSold/sold/mintedCount/nextTokenId…；总量 MAX_SUPPLY/maxSupply/cap/MAX_MINT/MAX_TOKENS/collectionSize/GEN_TOTAL…
     先读合约本身，再读它的 token()/nft()/animals()/collection() 子合约。
  2. 都读不到：扫合约码里的 PUSH4 选择器，用 openchain 签名库解析函数名（缓存 hotprogress_sel_cache.json），
     名字含 sold/minted 的无参函数求和当已铸，含 total/max/cap/supply/trigger 的当总量（如 CatMouse：gen0Sold+gen1Sold / GEN_TOTAL）。
  3. ERC20 型（有 decimals 且数值 ≥10^decimals）按 decimals 换算。
近 30 分钟增速：Transfer(from=0) 日志条数（publicnode，约 2400 块），读不到就用本地历史（hotprogress_state.json）差值。
读不到返回 {'ok': False, 'text': '进度读不到（原因）'}，调用方照写，不省略。
用法：python3 hotprogress.py <合约> [...]
"""
import json, os, re, sys, time, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from rpcpool import call_strict, rpc
import codeutil as CU
SC = os.path.join(D, 'hotprogress_sel_cache.json'); HS = os.path.join(D, 'hotprogress_state.json')
MINTED = ['totalMinted()', 'totalSupply()', 'minted()', 'totalSold()', 'sold()', 'mintedCount()', 'totalMintedCount()', 'numMinted()', 'mintCount()', 'currentSupply()', 'claimed()', 'totalClaimed()']
NEXTID = ['nextTokenId()', '_nextTokenId()', 'currentTokenId()', 'tokenIdCounter()']
CAP = ['MAX_SUPPLY()', 'maxSupply()', 'cap()', 'MAX_MINT()', 'MAX_TOKENS()', 'collectionSize()', 'maxMint()', 'MAX_MINTS()', 'TOTAL_SUPPLY()', 'maxTotalSupply()',
       'MAX_FREE()', 'maxFree()', 'FREE_SUPPLY()', 'GEN_TOTAL()', 'supplyCap()', 'MAX_CLAIMS()', 'maxClaims()']
CHILD = ['token()', 'nft()', 'animals()', 'collection()', 'NFT()', 'TOKEN()']
TR = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
TS1155 = '0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62'


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def u(a, sig):
    """('ok', int) / ('revert', None) / ('unknown', None)"""
    for i in range(2):
        st, r = call_strict(a, '0x' + CU.sel(sig))
        if st == 'ok':
            return ('ok', int(r[:66], 16)) if r and len(r) >= 66 else ('revert', None)
        if st == 'revert': return 'revert', None
        time.sleep(1 + i)
    return 'unknown', None


def first(a, sigs, skip=()):
    """一次批量读完 sigs（revert 的项为 None），返回第一个读到的。整批 RPC 失败重试 3 次，仍失败 unknown=True。"""
    from rpcpool import batch
    sigs = [x for x in sigs if x not in skip]
    for i in range(3):
        try:
            R = batch([('eth_call', [{'to': a, 'data': '0x' + CU.sel(x)}, 'latest']) for x in sigs], tolerant=True, timeout=6)
            for x, r in zip(sigs, R):
                if isinstance(r, str) and len(r) == 66 and int(r, 16) < 10 ** 40: return x, int(r, 16), False
            return None, None, False
        except Exception: time.sleep(1 + i)
    return None, None, True


def sel_names(code):
    cache = load(SC, {}); sels = sorted(set(re.findall(r'63([0-9a-f]{8})', code)))
    todo = [s for s in sels if s not in cache]
    for k in range(0, len(todo), 60):
        try:
            q = ','.join('0x' + s for s in todo[k:k + 60])
            r = json.load(urllib.request.urlopen(f'https://api.openchain.xyz/signature-database/v1/lookup?function={q}', timeout=15))['result']['function']
            for s in todo[k:k + 60]: cache[s] = ((r.get('0x' + s) or [{}])[0]).get('name') or ''
        except Exception: pass
    try: json.dump(cache, open(SC, 'w'))
    except Exception: pass
    return {s: cache.get(s, '') for s in sels}


def by_names(a):
    code = (rpc('eth_getCode', [a, 'latest']) or '0x').lower()
    nm = sel_names(code); sold, cap = [], []
    for s, n in nm.items():
        if not n.endswith('()'): continue
        b = n[:-2].lower()
        if re.search(r'sold|minted|claimed', b) and not re.search(r'max|cap|limit|per|left|remain', b): sold.append(n)
        elif re.search(r'^(max_?supply|gen_?total|total_?cap|cap|max_?mints?|max_?tokens|collection_?size|pool_?trigger)$', b): cap.append(n)
    s_vals = [(n, u(a, n)) for n in sold]; c_vals = [(n, u(a, n)) for n in cap]
    sv = [(n, v[1]) for n, v in s_vals if v[0] == 'ok' and v[1] is not None and v[1] < 10 ** 30]
    cv = sorted([(n, v[1]) for n, v in c_vals if v[0] == 'ok' and v[1]], key=lambda x: (0 if 'TOTAL' in x[0].upper() or 'MAX' in x[0].upper() else 1, -x[1]))
    if not sv: return None
    return {'minted': sum(v for _, v in sv), 'minted_src': '+'.join(n for n, _ in sv), 'cap': cv[0][1] if cv else None, 'cap_src': cv[0][0] if cv else None,
            'extra': {n: v for n, v in cv[1:]}}


def _logs(a, topics, f, t):
    for i in range(4):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request('https://bsc-rpc.publicnode.com', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs',
                'params': [{'address': a, 'topics': topics, 'fromBlock': hex(f), 'toBlock': hex(t)}]}).encode(), {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=10))
            if 'result' in d: return len(d['result'])
        except Exception: pass
        time.sleep(1.5 * (i + 1))
    # publicnode 限流时：48.club 500 块/段兜底
    n = 0; x = f
    while x <= t:
        y = min(x + 499, t); got = None
        for u_ in ('https://rpc-bsc.48.club', 'https://0.48.club'):
            try:
                d = json.load(urllib.request.urlopen(urllib.request.Request(u_, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs',
                    'params': [{'address': a, 'topics': topics, 'fromBlock': hex(x), 'toBlock': hex(y)}]}).encode(), {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=8))
                if 'result' in d: got = len(d['result']); break
            except Exception: pass
        if got is None: return None
        n += got; x = y + 1
    return n


def mint_logs_30m(addrs):
    """近 30 分钟（约 2400 块）Transfer(from=0) 条数，合约本身 + 子合约（token()/animals() 等）求和；ERC721/20 读失败返回 None。ERC1155 TransferSingle 尽力加上。"""
    try: head = int(rpc('eth_blockNumber', []), 16)
    except Exception: return None
    f = head - 2400; n = 0
    for a in addrs:
        k = _logs(a, [TR, None, '0x' + '0' * 64][:1] + [None, None] if False else [TR, '0x' + '0' * 64], f, head)
        if k is None: return None
        n += k
        k2 = _logs(a, [TS1155, None, '0x' + '0' * 64], f, head) if k == 0 else 0
        n += k2 or 0
    return n


def progress(c):
    c = c.lower(); out = {'contract': c}; why = []
    targets = [c]
    for s in CHILD:
        st, v = u(c, s)
        if st == 'ok' and v and v < 2 ** 160:
            ch = '0x' + hex(v)[2:].rjust(40, '0')
            if ch not in targets: targets.append(ch)
    res = None
    for a in targets:
        ms, mv, mu = first(a, MINTED)
        if ms is None:
            ns, nv, nu = first(a, NEXTID)
            if ns: ms, mv = ns + '-1', max(nv - 1, 0)
            mu = mu or nu
        cs, cv, cu = first(a, CAP)
        if ms is not None:
            res = {'minted': mv, 'minted_src': ms, 'cap': cv, 'cap_src': cs, 'at': a}; break
        if mu or cu: why.append(f'{a[:6]}…{a[-4:]} RPC 读取失败')
    if res is None or res['cap'] is None:
        for a in targets:
            try: r = by_names(a)
            except Exception: r = None
            if r and (res is None or (r.get('cap') and not res.get('cap'))): res = dict(r, at=a); break
    if res is None:
        out.update(ok=False, text='进度读不到（' + ('；'.join(why) or '无已铸/总量类函数') + '）'); return out
    a = res['at']; st, dec = u(a, 'decimals()')
    if st == 'ok' and dec and dec <= 36:
        for k in ('minted', 'cap'):
            if res.get(k) and res[k] >= 10 ** dec: res[k] = res[k] / 10 ** dec
    out.update(ok=True, **res)
    n30 = mint_logs_30m(targets)
    hist = load(HS, {}); h = hist.setdefault(c, []); now = time.time(); h.append([int(now), res['minted']]); del h[:-20]
    if n30 is None:
        base = next((x for x in h if now - x[0] <= 1900 and x is not h[-1]), None)
        if base and now - base[0] >= 120: out['rate30'] = f"+{round((res['minted'] - base[1]) * 1800 / (now - base[0]))}（按本地读数估算）"
        else: out['rate30'] = '增速读不到'
    else: out['rate30'] = f'+{n30} 次铸造'
    try: json.dump(hist, open(HS + '.tmp', 'w')); os.replace(HS + '.tmp', HS)
    except Exception: pass
    fm = lambda x: f'{x:,.0f}' if isinstance(x, (int, float)) and x >= 100 else (f'{x:g}' if isinstance(x, (int, float)) else '?')
    if res.get('cap'):
        pct = res['minted'] * 100 / res['cap']
        out['pct'] = round(pct, 1)
        out['text'] = f"进度 {fm(res['minted'])}/{fm(res['cap'])}（{pct:.1f}%）｜近 30 分钟 {out['rate30']}"
    else:
        out['text'] = f"已铸 {fm(res['minted'])}/总量读不到｜近 30 分钟 {out['rate30']}"
    if res.get('extra'): out['text'] += '｜' + '，'.join(f'{k[:-2]} {fm(v)}' for k, v in res['extra'].items())
    return out


if __name__ == '__main__':
    for a in sys.argv[1:]: print(json.dumps(progress(a), ensure_ascii=False))

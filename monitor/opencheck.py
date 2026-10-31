#!/usr/bin/env python3
"""持仓开盘提醒：对我们打过/持有的项目，检查代币是否已在 DEX 开池（有流动性）。
每个项目只提醒一次（seen.json 里记 open_alerted）。输出 JSON：{"new_open":[...], "checked":N}。
用法：python3 opencheck.py [--dry]   （--dry 不写回 seen.json）
"""
import json, sys, urllib.request
from rpcpool import batch

SEEN = '$AIDBOT_DATA/seen.json'
ACC = '$AIDBOT_HOME/baw/accounts/accounts.json'
V2 = '0xca143ce32fe78f1f7019d7d551a6402fc5350c73'
V3 = '0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865'
WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
USDT = '0x55d398326f99059ff775485246999027b3197955'
w = lambda a: a[2:].lower().rjust(64, '0')
DRY = '--dry' in sys.argv
MINLIQ = 50  # 流动性低于 50 美元视为未正式开盘


def ok(r):
    return isinstance(r, str) and r.startswith('0x') and len(r) > 2


def dexscreener(t):
    try:
        req = urllib.request.Request(f'https://api.dexscreener.com/latest/dex/tokens/{t}', headers={'User-Agent': 'Mozilla/5.0'})
        ps = json.load(urllib.request.urlopen(req, timeout=15)).get('pairs') or []
        ps = [p for p in ps if p.get('chainId') == 'bsc' and (p.get('liquidity') or {}).get('usd', 0) > 0]
        if not ps: return None
        p = max(ps, key=lambda p: p['liquidity']['usd'])
        return {'dex': p.get('dexId'), 'pair': p.get('pairAddress'), 'price_usd': p.get('priceUsd'),
                'liq_usd': p['liquidity']['usd'], 'fdv': p.get('fdv'), 'created': p.get('pairCreatedAt'), 'url': p.get('url')}
    except Exception:
        return None


def gecko(t):
    """2026-11-01 学  v2.4.5：DexScreener 没收录的池子（PancakeSwap Infinity、Uniswap v4、V3 等）用 GeckoTerminal 补查。"""
    try:
        req = urllib.request.Request(f'https://api.geckoterminal.com/api/v2/networks/bsc/tokens/{t}/pools', headers={'User-Agent': 'Mozilla/5.0', 'accept': 'application/json'})
        ps = []
        for p in json.load(urllib.request.urlopen(req, timeout=15)).get('data') or []:
            a = p.get('attributes') or {}; base = str(((p.get('relationships') or {}).get('base_token') or {}).get('data', {}).get('id', '')).lower().endswith(t)
            px = float((a.get('base_token_price_usd') if base else a.get('quote_token_price_usd')) or 0); liq = float(a.get('reserve_in_usd') or 0)
            if px > 0 and liq > 0: ps.append({'dex': str(((p.get('relationships') or {}).get('dex') or {}).get('data', {}).get('id', 'gecko')), 'pair': a.get('address'),
                                             'price_usd': px, 'liq_usd': liq, 'fdv': a.get('fdv_usd'), 'created': a.get('pool_created_at'),
                                             'url': f"https://www.geckoterminal.com/bsc/pools/{a.get('address')}", 'source': 'geckoterminal'})
        return max(ps, key=lambda x: x['liq_usd']) if ps else None
    except Exception:
        return None


USD1 = '0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d'
QUOTES = (WBNB, USDT, USD1)  # V2 池子常见报价币（学 v2.3.5：BNB/USDT 以外的配对也要查到）

S = json.load(open(SEEN)); A = json.load(open(ACC))
cands = [x for x in S if x.get('contract') and not x.get('open_alerted') and (x.get('accounts') or x.get('token'))]

# 1) 找代币地址：token 字段 > contract.token() > contract 本身（有 decimals 才算 ERC20）
calls = []
for x in cands:
    c = x['contract']
    calls += [('eth_call', [{'to': c, 'data': '0xfc0c546a'}, 'latest']), ('eth_call', [{'to': c, 'data': '0x313ce567'}, 'latest'])]
R = batch(calls, tolerant=True) if calls else []
items = []
for i, x in enumerate(cands):
    t = x.get('token')
    rt, rd = R[2 * i], R[2 * i + 1]
    if not t and ok(rt) and len(rt) >= 66 and int(rt, 16): t = '0x' + rt[-40:]
    if not t and ok(rd) and len(rd) >= 66 and int(rd, 16) <= 36: t = x['contract']
    if t: items.append((x, t.lower()))

# 2) 持仓（五号合计）+ V2 池子
calls = []
for x, t in items:
    calls += [('eth_call', [{'to': t, 'data': '0x70a08231' + w(a['bsc'])}, 'latest']) for a in A]
    calls += [('eth_call', [{'to': V2, 'data': '0xe6a43905' + w(t) + w(q)}, 'latest']) for q in QUOTES]
R = batch(calls, tolerant=True) if calls else []
n = len(A) + len(QUOTES); new = []
for i, (x, t) in enumerate(items):
    seg = R[i * n:(i + 1) * n]
    held = sum(int(r, 16) for r in seg[:len(A)] if ok(r))
    if held == 0: continue
    pair_onchain = next(('0x' + r[-40:] for r in seg[len(A):] if ok(r) and int(r, 16)), None)
    ds = dexscreener(t) or gecko(t)
    if not ds and pair_onchain:
        # 有池地址但 DexScreener 没收录：看储备
        try:
            res = batch([('eth_call', [{'to': pair_onchain, 'data': '0x0902f1ac'}, 'latest'])], tolerant=True)[0]
            if ok(res) and int(res[2:66], 16) and int(res[66:130], 16):
                ds = {'dex': 'pancakeswap v2', 'pair': pair_onchain, 'price_usd': None, 'liq_usd': None}
        except Exception: pass
    if not ds: continue
    if ds.get('liq_usd') is not None and ds['liq_usd'] < MINLIQ: continue  # 空池/测试池不算开盘
    try: dec = int(batch([('eth_call', [{'to': t, 'data': '0x313ce567'}, 'latest'])], tolerant=True)[0] or '0x12', 16)
    except Exception: dec = 18
    per = {}
    for a, r in zip(A, seg[:len(A)]):
        if ok(r) and int(r, 16): per[a['label']] = int(r, 16) / 10 ** dec
    new.append({'name': x.get('name'), 'contract': x['contract'], 'token': t, 'held_by_account': per, 'held_total': sum(per.values()),
                'value_usd': round(sum(per.values()) * float(ds['price_usd']), 2) if ds.get('price_usd') else None, **ds})
    if not DRY: x['open_alerted'] = True; x['opened_info'] = ds

if new and not DRY: json.dump(S, open(SEEN, 'w'), ensure_ascii=False, indent=1)
print(json.dumps({'checked': len(items), 'new_open': new}, ensure_ascii=False))

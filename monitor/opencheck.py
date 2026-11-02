#!/usr/bin/env python3
"""持仓开盘提醒：对我们打过/持有的项目，检查代币是否已在 DEX 开池（有流动性）。
每个项目只提醒一次（seen.json 里记 open_alerted）。输出 JSON：{"new_open":[...], "checked":N}。
用法：python3 opencheck.py [--dry]   （--dry 不写回 seen.json）
"""
import json, os, sys, time, threading, urllib.request
from rpcpool import batch as _batch
T0 = time.time()
LAST = '$AIDBOT_DATA/opencheck_last.json'
# 2026-11-02 修复（02:48–03:23、12:4x 连续 exit=124）：持仓候选 468 个 ×（6 号余额 + 3 个报价池）≈4200 个 eth_call，
# 原来 40 条一批串行、每节点 10 秒超时，公共节点限流时 >90 秒。现在：单次请求 6 秒超时、4 批并发、RPC 截止 T0+60 秒；
# DexScreener/Gecko 并发查；超过 75 秒还没完 → 看门狗输出上一轮成功结果（stale=true）并 exit=5，不留空文件、不写 seen.json。
OC_DEADLINE = T0 + float(os.environ.get('OC_RPC_BUDGET', '60'))
def batch(calls, tolerant=False):
    return _batch(calls, tolerant=tolerant, timeout=6, deadline=OC_DEADLINE, parallel=4)
def _fallback(reason):
    try: o = json.load(open(LAST))
    except Exception: o = {'checked': 0}
    o.update(new_open=[], stale=True, note=f'本轮未完成（{reason}），沿用上一轮结果；新开盘下一轮再查')
    os.write(1, (json.dumps(o, ensure_ascii=False) + '\n').encode()); os._exit(5)
_wd = threading.Timer(float(os.environ.get('OC_WATCHDOG', '75')), _fallback, args=('超过 75 秒',)); _wd.daemon = True; _wd.start()

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


DS_ERR = set(); PX_ERR = set()  # 2026-11-02：价格接口读失败的代币（≠ 没开池），输出 price_unknown，下一轮再查


def _get_retry(url, tries=3):
    last = None
    for i in range(tries):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'accept': 'application/json'}), timeout=12))
        except Exception as e:
            last = e
            if getattr(e, 'code', None) == 404: raise
            time.sleep(1.0 * 2 ** i)
    raise RuntimeError(str(last)[:120])


def dexscreener(t):
    try:
        try: ps = _get_retry(f'https://api.dexscreener.com/latest/dex/tokens/{t}').get('pairs') or []
        except Exception: PX_ERR.add(t); return None
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
        ps = []
        try: _d = _get_retry(f'https://api.geckoterminal.com/api/v2/networks/bsc/tokens/{t}/pools', tries=2)
        except Exception as e:
            if getattr(e, 'code', None) == 404: return None  # Gecko 没收录 = 确定没查到
            PX_ERR.add(t); return None
        for p in _d.get('data') or []:
            a = p.get('attributes') or {}; base = str(((p.get('relationships') or {}).get('base_token') or {}).get('data', {}).get('id', '')).lower().endswith(t)
            px = float((a.get('base_token_price_usd') if base else a.get('quote_token_price_usd')) or 0); liq = float(a.get('reserve_in_usd') or 0)
            if px > 0 and liq > 0: ps.append({'dex': str(((p.get('relationships') or {}).get('dex') or {}).get('data', {}).get('id', 'gecko')), 'pair': a.get('address'),
                                             'price_usd': px, 'liq_usd': liq, 'fdv': a.get('fdv_usd'), 'created': a.get('pool_created_at'),
                                             'url': f"https://www.geckoterminal.com/bsc/pools/{a.get('address')}", 'source': 'geckoterminal'})
        return max(ps, key=lambda x: x['liq_usd']) if ps else None
    except Exception:
        return None


_BNB = []
def bnb_usd():
    if not _BNB:
        try:
            req = urllib.request.Request('https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT', headers={'User-Agent': 'Mozilla/5.0'})
            _BNB.append(float(json.load(urllib.request.urlopen(req, timeout=10))['price']))
        except Exception:
            try:  # 币安 API 在本机所在地区被限制，回退用 DexScreener 的 WBNB 价格
                req = urllib.request.Request('https://api.dexscreener.com/latest/dex/tokens/' + WBNB, headers={'User-Agent': 'Mozilla/5.0'})
                ps = [p for p in json.load(urllib.request.urlopen(req, timeout=10)).get('pairs') or [] if p.get('chainId') == 'bsc' and p.get('baseToken', {}).get('address', '').lower() == WBNB]
                _BNB.append(float(max(ps, key=lambda p: (p.get('liquidity') or {}).get('usd', 0))['priceUsd']))
            except Exception:
                _BNB.append(0.0)
    return _BNB[0]


USD1 = '0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d'
QUOTES = (WBNB, USDT, USD1)  # V2 池子常见报价币（学 v2.3.5：BNB/USDT 以外的配对也要查到）

S = json.load(open(SEEN)); A = json.load(open(ACC))
cands = [x for x in S if x.get('contract') and not x.get('open_alerted') and (x.get('accounts') or x.get('token'))]

# 1) 找代币地址：token 字段 > contract.token() > contract 本身（有 decimals 才算 ERC20）
calls = []
for x in cands:
    c = x['contract']
    calls += [('eth_call', [{'to': c, 'data': '0xfc0c546a'}, 'latest']), ('eth_call', [{'to': c, 'data': '0x313ce567'}, 'latest'])]
try: R = batch(calls, tolerant=True) if calls else []
except Exception as e: _fallback(f'RPC 失败：{str(e)[:80]}')
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
try: R = batch(calls, tolerant=True) if calls else []
except Exception as e: _fallback(f'RPC 失败：{str(e)[:80]}')
n = len(A) + len(QUOTES); res_err = False; new = []; price_unknown = []; bal_unknown = []
# 2026-11-02：余额/池子读成 None（限流/节点错误）原来当「没持仓」直接跳过。现在把读空的项换节点重读一次，仍空的记 bal_unknown（下一轮再查）
_miss = [k for k, r in enumerate(R) if r is None]
if _miss and time.time() < OC_DEADLINE - 8:
    try:
        _R2 = _batch([calls[k] for k in _miss], tolerant=True, timeout=6, deadline=OC_DEADLINE, parallel=2)
        for k, r in zip(_miss, _R2): R[k] = r
    except Exception: pass
_held = [t for i, (x, t) in enumerate(items) if any(ok(r) and int(r, 16) for r in R[i * n:i * n + len(A)])]
from concurrent.futures import ThreadPoolExecutor
# 2026-11-02：改批量（DexScreener 30 个/次、Gecko multi 30 个/次），每批失败退避重试 3 次；读失败的进 PX_ERR（price_unknown），不当没开池
def _ds_batch(ts):
    try: ps = _get_retry('https://api.dexscreener.com/latest/dex/tokens/' + ','.join(ts)).get('pairs') or []
    except Exception: PX_ERR.update(ts); DS_ERR.update(ts); return {}
    o = {}
    for p in ps:
        tb = str((p.get('baseToken') or {}).get('address', '')).lower(); lq = (p.get('liquidity') or {}).get('usd', 0) or 0
        if p.get('chainId') != 'bsc' or tb not in ts or lq <= 0: continue
        if tb not in o or lq > o[tb]['liq_usd']:
            o[tb] = {'dex': p.get('dexId'), 'pair': p.get('pairAddress'), 'price_usd': p.get('priceUsd'), 'liq_usd': lq, 'fdv': p.get('fdv'), 'created': p.get('pairCreatedAt'), 'url': p.get('url')}
    return o
def _gk_batch(ts):
    try: ds = _get_retry('https://api.geckoterminal.com/api/v2/networks/bsc/tokens/multi/' + ','.join(ts), tries=2).get('data') or []
    except Exception: PX_ERR.update(ts); return {}
    o = {}
    for d in ds:
        a = d.get('attributes') or {}; tb = str(a.get('address', '')).lower(); lq = float(a.get('total_reserve_in_usd') or 0)
        if tb in ts and a.get('price_usd') and lq > 0:
            o[tb] = {'dex': 'gecko', 'pair': None, 'price_usd': float(a['price_usd']), 'liq_usd': lq, 'fdv': a.get('fdv_usd'), 'created': None,
                     'url': f'https://www.geckoterminal.com/bsc/tokens/{tb}', 'source': 'geckoterminal'}
    return o
DS = {}
with ThreadPoolExecutor(4) as _ex:
    for r in _ex.map(_ds_batch, [_held[k:k + 30] for k in range(0, len(_held), 30)]): DS.update(r)
_miss_px = [t for t in _held if t not in DS]
with ThreadPoolExecutor(2) as _ex:
    for r in _ex.map(_gk_batch, [_miss_px[k:k + 30] for k in range(0, len(_miss_px), 30)]): DS.update(r)
PX_ERR.difference_update(DS)
for t in _held: DS.setdefault(t, None)
for i, (x, t) in enumerate(items):
    seg = R[i * n:(i + 1) * n]
    held = sum(int(r, 16) for r in seg[:len(A)] if ok(r))
    if held == 0:
        _b = seg[:len(A)]
        if any(r is None for r in _b) and any(r is not None for r in _b): bal_unknown.append(x.get('name') or t)  # 部分号读到、部分号读空 = 读失败；全空多半是非代币合约 revert
        continue
    pair_onchain = next(('0x' + r[-40:] for r in seg[len(A):] if ok(r) and int(r, 16)), None)
    ds = DS.get(t) if t in DS else (dexscreener(t) or gecko(t))
    if not ds and pair_onchain:
        # 有池地址但 DexScreener 没收录：看储备
        try:
            res = batch([('eth_call', [{'to': pair_onchain, 'data': '0x0902f1ac'}, 'latest'])], tolerant=True)[0]
            if ok(res) and int(res[2:66], 16) and int(res[66:130], 16):
                ds = {'dex': 'pancakeswap v2', 'pair': pair_onchain, 'price_usd': None, 'liq_usd': None}
        except Exception: res_err = True
    # 只有 DexScreener 读失败、或池子储备读失败，才算「价格未知」（Gecko 免费接口常 429、空池子储备为 0 都不算）
    if not ds and (t in DS_ERR or (pair_onchain and 'res_err' in dir() and res_err)): price_unknown.append({'name': x.get('name'), 'token': t, 'pair_onchain': pair_onchain})
    res_err = False
    if not ds: continue
    if ds.get('liq_usd') is not None and ds['liq_usd'] < MINLIQ: continue  # 空池/测试池不算开盘
    try: dec = int(batch([('eth_call', [{'to': t, 'data': '0x313ce567'}, 'latest'])], tolerant=True)[0] or '0x12', 16)
    except Exception: dec = 18
    per = {}
    for a, r in zip(A, seg[:len(A)]):
        if ok(r) and int(r, 16): per[a['label']] = int(r, 16) / 10 ** dec
    val = round(sum(per.values()) * float(ds['price_usd']), 2) if ds.get('price_usd') else None
    # 盈亏（用户 2026-11-01 02:49 要求：开盘告诉我合约、赚了还是亏了）。成本取 seen 的 cost_usd，或 cost_bnb×BNB 价；免费铸造按 0。
    cost = x.get('cost_usd')
    if cost is None and x.get('cost_bnb') is not None: cost = float(x['cost_bnb']) * bnb_usd()
    if cost is None and str(x.get('type', '')).startswith('free'): cost = 0.0
    pnl = round(val - cost, 2) if (val is not None and cost is not None) else None
    pct = round(pnl / cost * 100, 1) if (pnl is not None and cost) else None
    # 用户 2026-11-01 03:03：开盘要告诉我「我打的基础价格和基础市值」。基础价=成本÷持仓枚数；市值按 totalSupply 算。
    try: sup = int(batch([('eth_call', [{'to': t, 'data': '0x18160ddd'}, 'latest'])], tolerant=True)[0], 16) / 10 ** dec
    except Exception: sup = None
    held_n = sum(per.values())
    entry_px = (cost / held_n) if (cost and held_n) else None
    cur_px = float(ds['price_usd']) if ds.get('price_usd') else None
    basis = {'total_supply': sup, 'entry_price_usd': entry_px, 'entry_mcap_usd': round(entry_px * sup, 0) if (entry_px and sup) else None,
             'cur_mcap_usd': round(cur_px * sup, 0) if (cur_px and sup) else None, 'x_vs_entry': round(cur_px / entry_px, 2) if (cur_px and entry_px) else None}
    new.append({**basis, 'name': x.get('name'), 'contract': x['contract'], 'token': t, 'held_by_account': per, 'held_total': sum(per.values()),
                'value_usd': val, 'cost_usd': round(cost, 2) if cost is not None else None, 'pnl_usd': pnl, 'pnl_pct': pct,
                'verdict': None if pnl is None else ('赚' if pnl > 0 else '亏' if pnl < 0 else '持平'), **ds})
    if not DRY: x['open_alerted'] = True; x['opened_info'] = ds

_wd.cancel()
if new and not DRY: json.dump(S, open(SEEN + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(SEEN + '.tmp', SEEN)
out = {'checked': len(items), 'held': len(_held), 'new_open': new, 'elapsed_s': round(time.time() - T0, 1),
       'price_unknown': price_unknown, 'balance_unknown': bal_unknown[:30], 'balance_unknown_n': len(bal_unknown)}
if not DRY: json.dump(dict(out, new_open=[], time=time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))), open(LAST, 'w'), ensure_ascii=False)
print(json.dumps(out, ensure_ascii=False))

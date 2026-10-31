#!/usr/bin/env python3
"""Agent ID 内盘新币监控：Four.meme OpenFour Agent ID 专区（币安 pulse 榜）+ Gang.fun 新币 + BIBIAI LaunchPad（2026-11-01 学  v2.5.3：AGENT ID 用 已售/开池份数 才读得到进度，我们 fair mint 扫描漏了它）。
只读；输出上次以来新出现的币 + 进度跳升的币。状态存 state.json。用法: python3 platforms.py"""
import json, os, time, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(os.environ['AIDBOT_DATA'], 'platforms') if os.environ.get('AIDBOT_DATA') else D; os.makedirs(SD, exist_ok=True); ST = os.path.join(SD, 'state.json')
UA = {'user-agent': 'Mozilla/5.0', 'content-type': 'application/json'}
PULSE = 'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list/ai'
def get(url, body=None):
    last = None
    for i, t in enumerate((12, 20, 30)):  # 超时/5xx 自动重试 3 次
        try:
            r = urllib.request.Request(url, json.dumps(body).encode() if body else None, UA)
            return json.load(urllib.request.urlopen(r, timeout=t))
        except Exception as e:
            last = e; time.sleep(2 * (i + 1))
    raise last
def four(stage):
    d = get(PULSE, {'chainId': '56', 'rankType': stage, 'protocol': [2004], 'limit': 50,
                    'tokenVersionFilter': {'2004': ['openfour.meme.AgentIdExclusive']}})
    out = []
    for x in d.get('data') or []:
        if x.get('tokenVersion') != 'openfour.meme.AgentIdExclusive' or x.get('migrateStatus') != 0: continue
        out.append({'ca': x['contractAddress'].lower(), 'sym': x.get('symbol'), 'name': x.get('name'), 'progress': x.get('progress'),
                    'holders': x.get('holders'), 'liq': x.get('liquidity'), 'tax_buy': x.get('taxRateBuy'), 'tax_sell': x.get('taxRateSell'),
                    'created': x.get('createTime'), 'link': f"https://four.meme/token/{x['contractAddress']}"})
    return out
def gang():
    d = get('https://gang.fun/api/tokens?sort=new&limit=50&offset=0'); out = []
    for x in d.get('items') or []:
        ca = (x.get('address') or x.get('tokenAddress') or x.get('contractAddress') or '').lower()
        if not ca: continue
        out.append({'ca': ca, 'sym': x.get('symbol') or x.get('ticker'), 'name': x.get('name'),
                    'progress': x.get('progressPct'), 'creator': x.get('creatorHandle') or x.get('creator'), 'holders': x.get('holders'), 'fdv_usd': x.get('fdvUsd'), 'website': x.get('website'), 'twitter': x.get('twitter'),
                    'created': x.get('createdAt'), 'link': f'https://gang.fun/token/{ca}'})
    return out
def bibiai():
    d = get('https://launch.bibiai.meme/api/projects?limit=50&page=1'); out = []
    for x in (d.get('data') or {}).get('items') or []:
        ca = (x.get('mint') or '').lower()
        if not ca: continue
        thr = int(x.get('openThreshold') or 0); sold = int(x.get('totalMinted') or 0)
        q = 'BNB' if x.get('nativePayment') else (x.get('quoteSymbol') or '')
        out.append({'ca': ca, 'sym': x.get('symbol'), 'name': x.get('name'), 'progress': round(sold * 100 / thr, 1) if thr else None,
                    'sold': sold, 'open_at': thr, 'opened': x.get('opened'), 'status': x.get('status'), 'paused': x.get('paused'),
                    'price': int(x.get('price') or x.get('basePrice') or 0) / 1e18, 'quote': q, 'per_agent': x.get('agentQuota'),
                    'token': x.get('token'), 'pair': x.get('pair'), 'treasury_bps': x.get('treasuryBps'), 'liquidity_bps': x.get('liquidityBps'),
                    'buy_tax_bps': x.get('buyTaxBps'), 'sell_tax_bps': x.get('sellTaxBps'), 'renounced': x.get('renounced'),
                    'refund_deadline': x.get('refundDeadline'), 'creator': x.get('creator'), 'holders': x.get('holderCount'), 'created': x.get('createdAt'),
                    'link': f'https://launch.bibiai.meme/project/{ca}', 'bscscan': f'https://bscscan.com/address/{ca}'})
    return out
st = json.load(open(ST)) if os.path.exists(ST) else {}
res = {'errors': []}
for key, fn in [('four_new', lambda: four(10)), ('four_progress', lambda: four(20)), ('gang_new', gang), ('bibiai', bibiai)]:
    try: rows = fn()
    except Exception as e: res['errors'].append(f'{key}: {e}'); continue
    seen = st.setdefault(key, {}); new, jump = [], []
    for r in rows:
        p = float(r.get('progress') or 0)
        if r['ca'] not in seen: new.append(r)
        elif p - seen[r['ca']] >= 15: r['progress_prev'] = seen[r['ca']]; jump.append(r)
        seen[r['ca']] = p
    if key == 'bibiai':  # 未开池且进度 ≥60% 的单独列出，巡逻按 60% 提醒 / 70% 打满规则处理
        res['bibiai_hot'] = [r for r in rows if not r.get('opened') and (r.get('progress') or 0) >= 60]
    res[key] = {'total': len(rows), 'new': new if st.get('_init_' + key) else f'首次运行，记录 {len(new)} 个基线', 'jump': jump}
    st['_init_' + key] = 1
json.dump(st, open(ST, 'w'))
print(json.dumps(res, ensure_ascii=False, indent=1))

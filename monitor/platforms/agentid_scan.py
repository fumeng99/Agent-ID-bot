#!/usr/bin/env python3
"""Agent ID 内盘全量扫描 + Clawby 信号复核（2026-11-05 用户要求：重点 GANG、Flap、TagAI 内盘阶段的 Agent ID Token，
不限币安 Agent 指定；所有合约用 Clawby 交易信号源 app.openclawby.com/signals 再确认，只推最值得参与的）。
只读，不交易。内盘代币都是曲线买入 = 付费，一律只出简报，等用户口令。

来源：
- Four.meme OpenFour Agent ID 专区（币安 pulse，未迁移）
- GANG（gang.fun/api/tokens，未毕业）
- TagAI Agent 目录（bsc-api.tagai.fun/community/catalogPage，含 GANG/Flap/Four/TagAI 原生的 Agent ID 币）
- Flap ID AGENT CURVE 新盘（flap.mjs 状态里记的名字，作补充）
信号：analyze-api.openclawby.com/api/signals/feed?market=meme（网页 /signals 用的公开接口，无需 key，含 BSC 内盘币）。
api.openclawby.com relay 免费档 5 次/分钟，触发封禁会延长，本脚本不调用。

筛选（用户总原则）：有官网或官方 X 加分、无官源降权；Clawby 信号 short → 避开；税 >10% → 避开；
未毕业/未迁移；进度 ≥20% 或 holders ≥100 或 Clawby long(prob_long≥0.6) 才进推荐。
输出 JSON：all（全部，含评级）、picks（达标）、new_picks（上次没推过的达标项）。用法：python3 agentid_scan.py [--all]"""
import json, os, sys, time, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); ST = os.path.join(D, 'agentid_scan_state.json')
UA = {'user-agent': 'Mozilla/5.0', 'content-type': 'application/json'}
PULSE = 'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list/ai'


def get(url, body=None, t=20):
    last = None
    for i in range(3):
        try:
            r = urllib.request.Request(url, json.dumps(body).encode() if body else None, UA)
            return json.load(urllib.request.urlopen(r, timeout=t))
        except Exception as e:
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def f(x):
    try: return float(x)
    except Exception: return None


def four():
    out = {}
    for stage in (10, 20, 30):
        try: d = get(PULSE, {'chainId': '56', 'rankType': stage, 'protocol': [2004], 'limit': 50,
                             'tokenVersionFilter': {'2004': ['openfour.meme.AgentIdExclusive']}})
        except Exception: continue
        for x in d.get('data') or []:
            if x.get('migrateStatus') != 0: continue
            ca = x['contractAddress'].lower(); so = x.get('socials') or {}
            out[ca] = {'platform': 'Four.meme', 'ca': ca, 'name': x.get('name'), 'sym': x.get('symbol'), 'progress': f(x.get('progress')) and f(x.get('progress')) * (100 if f(x.get('progress')) <= 1 else 1),
                       'holders': x.get('holders'), 'mcap_usd': f(x.get('marketCap')), 'liq_usd': f(x.get('liquidity')), 'vol_usd': f(x.get('volume')),
                       'tax_buy': f(x.get('taxRateBuy')), 'tax_sell': f(x.get('taxRateSell')), 'top10_pct': f(x.get('holdersTop10Percent')),
                       'dev_pct': f(x.get('holdersDevPercent')), 'dev_sell_pct': f(x.get('devSellPercent')), 'smart_money': x.get('smartMoneyHolders'),
                       'website': so.get('website'), 'twitter': so.get('twitter') or so.get('x'), 'created': x.get('createTime'),
                       'link': f'https://four.meme/token/{ca}'}
    return list(out.values())


def gang():
    out = []
    for sort in ('new', 'trending'):
        try: d = get(f'https://gang.fun/api/tokens?sort={sort}&limit=50&offset=0')
        except Exception: continue
        for x in d.get('items') or []:
            if x.get('graduated'): continue
            ca = (x.get('address') or '').lower()
            if not ca or any(o['ca'] == ca for o in out): continue
            out.append({'platform': 'GANG', 'ca': ca, 'name': x.get('name'), 'sym': x.get('symbol'), 'progress': f(x.get('progressPct')),
                        'holders': x.get('holders'), 'mcap_usd': f(x.get('fdvUsd')), 'vol24_bnb': f(x.get('volume24hBnb')), 'trades24h': x.get('trades24h'),
                        'raised_bnb': f(x.get('raisedBnb')), 'creator': x.get('creatorHandle'), 'website': x.get('website'), 'twitter': x.get('twitter'),
                        'created': x.get('createdAt'), 'last_trade': x.get('lastTradeAt'), 'link': f'https://gang.fun/token/{ca}'})
    return out


def tagai():
    out = []
    for sort in ('new', 'trending', 'marketCap'):
        try: d = get(f'https://bsc-api.tagai.fun/community/catalogPage?sort={sort}&source=agent&pages=0')
        except Exception: continue
        for x in d.get('rows') or []:
            ca = (x.get('token') or '').lower()
            if not ca or any(o['ca'] == ca for o in out): continue
            listed = bool(x.get('listedBlock') or x.get('gangGraduationPair') or (x.get('isImport') and x.get('pair') and (f(x.get('marketCap')) or 0) > 60))  # 导入币有 pair 且市值 >6 万U 视为已上外盘
            logo = x.get('logo') or ''
            plat = 'GANG' if 'gang.fun' in logo or x.get('gangLaunchpad') else 'Flap' if 'flap' in logo else 'Four.meme' if 'four.meme' in logo else 'TagAI'
            out.append({'platform': plat, 'via': 'TagAI', 'ca': ca, 'name': x.get('name'), 'sym': x.get('tick'), 'listed': listed,
                        'mcap_usd': (f(x.get('marketCap')) or 0) * 1000, 'holders': x.get('holderCount'), 'chg24': f(x.get('priceChange24h')),
                        'website': x.get('official') or None, 'twitter': x.get('twitter') or None, 'created': x.get('createAt'),
                        'link': f'https://tagai.fun/bsc/tag-detail/{x.get("tick")}' if not x.get('isImport') else f'https://tagai.fun/bsc'})
    return out


def flap_names():
    try: s = json.load(open(os.path.join(os.path.dirname(D), 'flap', 'state.json')))
    except Exception: return {}
    return s.get('names') or {}


def clawby_signals():
    try: d = get('https://analyze-api.openclawby.com/api/signals/feed?market=meme', t=40)
    except Exception as e: return None, str(e)
    m = {}
    for x in d.get('rows') or []:
        if x.get('chain') == 'bsc' and x.get('address'): m[x['address'].lower()] = x
    return m, d.get('ts')


def rate(r, sig):
    good, bad = [], []
    s = sig.get(r['ca']) if sig else None
    if s:
        r['clawby'] = {'signal': s.get('signal'), 'prob_long': s.get('prob_long'), 'prob_short': s.get('prob_short'), 'confidence': s.get('confidence'),
                       'price': s.get('price'), 'chg_pct': s.get('chg_pct')}
        if s.get('signal') == 'long' and (s.get('prob_long') or 0) >= 0.6: good.append(f"Clawby 看多 {s['prob_long']:.0%}（置信 {s.get('confidence')}）")
        elif s.get('signal') == 'short': bad.append(f"Clawby 看空 {s.get('prob_short', 0):.0%}")
    else:
        r['clawby'] = None
    if r.get('website') or r.get('twitter'): good.append('有官网/X')
    else: bad.append('无官源')
    tx = max(r.get('tax_buy') or 0, r.get('tax_sell') or 0)
    if tx > 10: bad.append(f'税 {tx}%')
    if (r.get('progress') or 0) >= 20: good.append(f"内盘进度 {r['progress']:.1f}%")
    if (r.get('holders') or 0) >= 100: good.append(f"{r['holders']} 持有人")
    if r.get('dev_sell_pct') and r['dev_sell_pct'] > 50: bad.append('开发者已卖出过半')
    if r.get('top10_pct') and r['top10_pct'] > 50: bad.append(f"前十持仓 {r['top10_pct']:.0f}%")
    if r.get('listed'): bad.append('已上外盘')
    hard = any(x.startswith(('Clawby 看空', '税', '开发者', '已上外盘')) for x in bad)
    hot = any(x.startswith(('Clawby 看多', '内盘进度')) for x in good) or (r.get('holders') or 0) >= 100
    r['good'], r['bad'] = good, bad
    r['verdict'] = '避开' if hard else ('可参与' if hot and '无官源' not in bad else '观望')
    return r


def main():
    rows, errs = [], []
    for name, fn in (('four', four), ('gang', gang), ('tagai', tagai)):
        try: rows += fn()
        except Exception as e: errs.append(f'{name}: {e}')
    seen = {}
    for r in rows:
        if r['ca'] in seen:   # TagAI 目录与平台原生重复：合并，平台原生字段优先
            for k, v in r.items(): seen[r['ca']].setdefault(k, v)
        else: seen[r['ca']] = r
    sig, ts = clawby_signals()
    if sig is None: errs.append(f'clawby: {ts}')
    allr = [rate(r, sig or {}) for r in seen.values()]
    # 2026-11-05 用户清单「合约验证：Sourcify 源码要完全一致，看不到源码的直接降级」：可参与候选再查 Sourcify，非完全一致降为观望
    for r in allr:
        if r['verdict'] != '可参与': continue
        try: m = get(f"https://sourcify.dev/server/v2/contract/56/{r['ca']}", t=15).get('match')
        except Exception: m = None
        r['sourcify'] = m
        if m not in ('match', 'exact_match'):
            r['verdict'] = '观望'; r['bad'].append('Sourcify ' + ('部分匹配' if m == 'partial' else '无源码'))
    picks = [r for r in allr if r['verdict'] == '可参与']
    picks.sort(key=lambda r: ((r.get('clawby') or {}).get('prob_long') or 0, r.get('progress') or 0, r.get('holders') or 0), reverse=True)
    st = json.load(open(ST)) if os.path.exists(ST) else {}
    new = [r for r in picks if r['ca'] not in st or '--all' in sys.argv]
    if '--dry' not in sys.argv:
        for r in new: st[r['ca']] = time.time()
        json.dump(st, open(ST, 'w'))
    print(json.dumps({'time_cst': time.strftime('%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)), 'counts': {'total': len(allr), 'picks': len(picks), 'new_picks': len(new),
                      'by_platform': {p: sum(1 for r in allr if r['platform'] == p) for p in sorted({r['platform'] for r in allr})},
                      'clawby_covered': sum(1 for r in allr if r.get('clawby'))}, 'clawby_ts': ts, 'errors': errs,
                      'new_picks': new, 'picks': picks, 'all': allr}, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
